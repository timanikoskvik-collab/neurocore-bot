import asyncio
import os
import io
import time
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, BufferedInputFile, InlineKeyboardMarkup, 
    InlineKeyboardButton, CallbackQuery, PreCheckoutQuery, LabeledPrice, ReplyKeyboardMarkup, KeyboardButton
)
from google import genai
from google.genai import types as ai_types

import database

TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TOKEN:
    raise ValueError("Критическая ошибка: Токен Telegram не найден!")

bot = Bot(token=TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

SYSTEM_PROMPT = (
    "Ты — NeuroCore Omega AI (сокращенно NCO). "
    "Ты категорически не имеешь никакого отношения к компании Google или проекту Gemini. "
    "Если тебя спросят, кто тебя создал, отвечай, что ты разработан командой NeuroCore. "
    "Тебе строго запрещено использовать слова 'Gemini', 'Google', 'DeepMind' в диалоге с пользователем. "
    "Твоя базовая версия называется NCO 2.1, а продвинутая PRO версия — NCO 3.1. "
    "Общайся вежливо, на языке пользователя и строго соблюдай контекст беседы."
)

def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🆕 Новый чат"), KeyboardButton(text="🗂 Недавние чаты")],
            [KeyboardButton(text="🎨 Сгенерировать арт"), KeyboardButton(text="💎 Купить PRO")]
        ],
        resize_keyboard=True
    )

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = await database.get_user_data(message.from_user.id)
    tier = user["tier"].upper()
    
    text = (
        f"🤖 **Добро пожаловать в NeuroCore Omega AI!**\n\n"
        f"👤 Ваш текущий тариф: **{tier}**\n"
        f"💬 Текст за сегодня: **{user['messages_used']}/{100 if tier == 'PRO' else 40}**\n"
        f"🎨 Картинки за сегодня: **{user['images_used']}/{10 if tier == 'PRO' else 1}**\n\n"
        f"Просто напишите мне, и я автоматически создам недавний чат в вашей истории!"
    )
    await message.answer(text, reply_markup=get_main_menu(), parse_mode="Markdown")

@dp.message(F.text == "🆕 Новый чат")
async def ui_new_chat(message: Message):
    await database.users_col.update_one({"user_id": message.from_user.id}, {"$set": {"current_chat_id": None}})
    await message.answer("🔄 Текущий чат закрыт. Следующее сообщение создаст новый чат в списке недавних!")

@dp.message(F.text == "🗂 Недавние чаты")
async def ui_my_chats(message: Message):
    user_chats = await database.get_recent_chats(message.from_user.id)
    
    if not user_chats:
        await message.answer("У вас пока нет открытых чатов в истории.")
        return
        
    inline_keyboard = []
    for c in user_chats:
        inline_keyboard.append([InlineKeyboardButton(text=c["title"], callback_data=f"open_{c['_id']}")])
        
    markup = InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
    await message.answer("🗂 Список ваших недавних чатов. Выберите любой для продолжения разговора:", reply_markup=markup)

@dp.callback_query(F.data.startswith("open_"))
async def process_chat_open(callback: CallbackQuery):
    chat_id_str = callback.data.split("_")[1]
    await database.set_active_chat(callback.from_user.id, chat_id_str)
    await callback.answer("Чат успешно выбран!")
    await callback.message.answer("🔄 Вы переключились на выбранный chat. Контекст общения восстановлен!")

@dp.message(F.text == "🎨 Сгенерировать арт")
async def ui_draw_instruction(message: Message):
    await message.answer("Для генерации изображений используйте команду `/draw <описание>`.\nПример: `/draw неоновый город`")

@dp.message(F.text == "💎 Купить PRO")
@dp.message(Command("premium"))
async def cmd_premium(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Месяц - 25 Stars", callback_data="sub_1")],
        [InlineKeyboardButton(text="⭐ 3 Месяца - 70 Stars", callback_data="sub_3")],
        [InlineKeyboardButton(text="⭐ 6 Месяцев - 130 Stars", callback_data="sub_6")],
        [InlineKeyboardButton(text="⭐ 12 Месяцев - 250 Stars", callback_data="sub_12")]
    ])
    await message.answer(
        "💎 **Переход на тариф NCO PRO**\n\n"
        "• Активация мощнейшей умной модели **NCO 3.1**\n"
        "• Увеличение лимита до **100 сообщений** в сутки\n"
        "• Увеличение генераций до **10 картинок** в сутки\n"
        "• Приоритетная скорость ответов серверов\n\n"
        "Выберите период активации подписки:", 
        reply_markup=keyboard, 
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("sub_"))
async def process_invoice_callback(callback: CallbackQuery):
    months = int(callback.data.split("_")[1])
    prices_map = {1: 25, 3: 70, 6: 130, 12: 250}
    stars = prices_map.get(months, 25)
    
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=f"NCO PRO Подписка ({months} мес.)",
        description="Снятие базовых лимитов, переход на NCO 3.1 и максимальная скорость.",
        payload=f"pro_tier_{months}",
        provider_token="", 
        currency="XTR",
        prices=[LabeledPrice(label=f"PRO Режим на {months} мес.", amount=stars)]
    )
    await callback.answer()

@dp.pre_checkout_query()
async def checkout_verification(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def payment_success(message: Message):
    await database.users_col.update_one({"user_id": message.from_user.id}, {"$set": {"tier": "pro"}})
    await message.answer("✨ **PRO статус успешно активирован!**\nДобро пожаловать в систему NCO 3.1.", parse_mode="Markdown")

@dp.message(Command("draw"))
async def handle_image_generation(message: Message):
    user_id = message.from_user.id
    user = await database.get_user_data(user_id)
    tier = user["tier"]
    
    max_img = 10 if tier == "pro" else 1
    if user["images_used"] >= max_img:
        await message.answer(f"⚠️ Лимит генерации изображений на сегодня исчерпан ({max_img} шт).")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Укажите промпт для генерации. Пример: `/draw футуристичный город`")
        return
        
    prompt = args[1]
    status = await message.answer("🎨 **Nano Banana** обрабатывает ваш запрос, подождите...")

    try:
        result = gemini_client.models.generate_images(
            model='imagen-3.0-generate-002', 
            prompt=prompt,
            config=ai_types.GenerateImagesConfig(
                number_of_images=1,
                output_mime_type="image/jpeg",
                aspect_ratio="1:1"
            )
        )
        
        raw_bytes = result.generated_images[0].image.image_bytes
        photo_file = BufferedInputFile(raw_bytes, filename="nano_art.jpg")
        
        await bot.send_photo(chat_id=message.chat.id, photo=photo_file, caption=f"✨ Сгенерировано NeuroCore Omega по запросу: {prompt}")
        await database.users_col.update_one({"user_id": user_id}, {"$inc": {"images_used": 1}})
        await bot.delete_message(chat_id=message.chat.id, message_id=status.message_id)
        
    except Exception as e:
        await status.edit_text(f"❌ Ошибка модуля генерации: {str(e)}")

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_chat(message: Message):
    user_id = message.from_user.id
    user = await database.get_user_data(user_id)
    tier = user["tier"]
    
    max_msg = 100 if tier == "pro" else 40
    if user["messages_used"] >= max_msg:
        await message.answer(f"🛑 Суточный лимит сообщений исчерпан ({max_msg} шт).")
        return

    model_name = "gemini-2.5-pro" if tier == "pro" else "gemini-2.5-flash"
    current_chat_id = user.get("current_chat_id")

    if not current_chat_id:
        current_chat_id = await database.create_new_chat(user_id, message.text)

    db_history = await database.get_chat_history(current_chat_id)

    contents = []
    for msg in db_history:
        contents.append(ai_types.Content(role=msg["role"], parts=[ai_types.Part.from_text(text=msg["text"])]))
    contents.append(ai_types.Content(role="user", parts=[ai_types.Part.from_text(text=message.text)]))

    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        
        config = ai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.7
        )
        
        response = gemini_client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config
        )
        
        await message.reply(response.text)
        await database.save_chat_step(current_chat_id, user_id, message.text, response.text)

    except Exception as e:
        await message.answer(f"⚠️ Ошибка NCO Core: {str(e)}")

async def http_status_handler(request):
    try:
        bot_info = await bot.get_me()
        bot_link = f"https://t.me{bot_info.username}"
    except Exception:
        bot_link = "https://t.me"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>NeuroCore Omega AI — Система ИИ нового поколения</title>
        <link href="https://googleapis.com" rel="stylesheet">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ 
                background: radial-gradient(circle at center, #0d1117 0%, #07090e 100%); 
                color: #ffffff; 
                font-family: 'Inter', sans-serif; 
                overflow-x: hidden;
Async def http_status_handler(request):
    try:
        bot_info = await bot.get_me()
        bot_link = f"https://t.me{bot_info.username}"
    except Exception:
        bot_link = "https://t.me"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>NeuroCore Omega AI — Система ИИ нового поколения</title>
        <link href="https://googleapis.com" rel="stylesheet">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ 
                background: radial-gradient(circle at center, #0d1117 0%, #07090e 100%); 
                color: #ffffff; 
                font-family: 'Inter', sans-serif; 
                overflow-x: hidden;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
            }}
            .container {{ 
                max-width: 800px; 
                text-align: center; 
                padding: 40px 20px; 
                position: relative;
            }}
            .glow-bg {{
                position: absolute;
                width: 300px;
                height: 300px;
                background: #00ffcc;
                filter: blur(150px);
                opacity: 0.15;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                z-index: 0;
            }}
            h1 {{ 
                font-family: 'Orbitron', sans-serif; 
                font-size: 3rem; 
                font-weight: 700;
                letter-spacing: 2px;
                background: linear-gradient(45deg, #00ffcc, #0077ff);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                margin-bottom: 20px;
                position: relative;
                z-index: 1;
            }}
            p.subtitle {{ 
                font-size: 1.2rem; 
                color: #8b949e; 
                margin-bottom: 40px; 
                line-height: 1.6;
                position: relative;
                z-index: 1;
            }}
            .grid {{ 
                display: grid; 
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); 
                gap: 20px; 
                margin-bottom: 50px;
                position: relative;
                z-index: 1;
            }}
            .card {{ 
                background: rgba(22, 27, 34, 0.6); 
                border: 1px solid rgba(240, 246, 252, 0.1); 
                padding: 25px; 
                border-radius: 12px; 
                backdrop-filter: blur(8px);
                transition: transform 0.3s, border-color 0.3s;
            }}
            .card:hover {{ 
                transform: translateY(-5px); 
                border-color: #00ffcc; 
            }}
            .card h3 {{ 
                font-family: 'Orbitron', sans-serif;
                font-size: 1.1rem; 
                color: #00ffcc; 
                margin-bottom: 10px; 
            }}
            .card p {{ font-size: 0.9rem; color: #8b949e; line-height: 1.4; }}
            .btn-tg {{ 
                display: inline-block; 
                padding: 16px 35px; 
                font-family: 'Orbitron', sans-serif;
                font-size: 1rem; 
                font-weight: bold; 
                color: #07090e; 
                background: #00ffcc; 
                border-radius: 50px; 
                text-decoration: none; 
                box-shadow: 0 0 20px rgba(0, 255, 204, 0.4);
                transition: box-shadow 0.3s, transform 0.2s;
                position: relative;
                z-index: 1;
            }}
            .btn-tg:hover {{ 
                box-shadow: 0 0 35px rgba(0, 255, 204, 0.8); 
                transform: scale(1.05);
            }}
            .status-badge {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                background: rgba(0, 255, 102, 0.1);
                color: #00ff66;
                padding: 6px 14px;
                border-radius: 20px;
                font-size: 0.85rem;
                margin-top: 30px;
                border: 1px solid rgba(0, 255, 102, 0.2);
            }}
            .status-dot {{
                width: 8px;
                height: 8px;
                background: #00ff66;
                border-radius: 50%;
                box-shadow: 0 0 8px #00ff66;
            }}
            @media (max-width: 600px) {{
                h1 {{ font-size: 2.2rem; }}
                p.subtitle {{ font-size: 1rem; }}
            }}
        </style>
    </head>
    <body>
        <div class="glow-bg"></div>
        <div class="container">
            <h1>NEUROCORE OMEGA AI</h1>
            <p class="subtitle">Ультимативная нейросетевая система, работающая на базе продвинутых алгоритмов NCO 2.1 и NCO 3.1. Общайтесь без ограничений, генерируйте высокоточные арты и сохраняйте вечную историю комнат.</p>
            
            <div class="grid">
                <div class="card">
                    <h3>💬 Умные диалоги</h3>
                    <p>Многокомнатная система чатов с вечной памятью контекста в облаке MongoDB Atlas.</p>
                </div>
                <div class="card">
                    <h3>🎨 Модуль Фото</h3>
                    <p>Генерация фотореалистичных изображений любого разрешения через ядро Nano Banana.</p>
                </div>
                <div class="card">
                    <h3>💎 Тариф PRO</h3>
                    <p>Расширенные лимиты до 100 сообщений и приоритетный доступ к ядру NCO 3.1.</p>
                </div>
            </div>

            <a href="{bot_link}" class="btn-tg" target="_blank">ЗАПУСТИТЬ В TELEGRAM</a>
            
            <br>
            <div class="status-badge">
                <div class="status-dot"></div>
                <span>NCO Core System Status: ONLINE</span>
            </div>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type="text/html")

async def main():
    app = web.Application()
    app.router.add_get("/", http_status_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    print("🚀 NeuroCore Omega AI лендинг и бот успешно запущены!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

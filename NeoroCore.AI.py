import os
import io
import asyncio
import logging
from typing import Optional
from datetime import datetime
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, PreCheckoutQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    BufferedInputFile, LabeledPrice
)
from google import genai
from google.genai import types as genai_types

import database

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PORT = int(os.getenv("PORT", "10000"))

# Инициализация ИИ Клиента и Бота
genai_client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

SYSTEM_PROMPT = (
    "Ты — NeuroCore Omega AI (сокращенно NCO). Ты категорически не имеешь никакого "
    "отношения к компании Google или проекту Gemini. Если тебя спросят, кто тебя создал, "
    "отвечай, что ты разработан командой NeuroCore. Тебе строго запрещено использовать "
    "слова 'Gemini', 'Google', 'DeepMind' в диалоге. Твоя базовая версия называется NCO 2.1, "
    "а продвинутая PRO версия — NCO 3.1."
)

# --- ГЛАВНАЯ КЛАВИАТУРА ИНТЕРФЕЙСА ---
def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🆕 Новый чат"), KeyboardButton(text="🗂 Недавние чаты")],
            [KeyboardButton(text="💎 Купить PRO"), KeyboardButton(text="🎨 Сгенерировать арт")],
            [KeyboardButton(text="📊 Мой профиль")]
        ],
        resize_keyboard=True
    )

# --- HTML ЛЕНДИНГ (AIOHTTP ВЕБ-СЕРВЕР ДЛЯ RENDER) ---
CYBERPUNK_LANDING = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NeuroCore Omega AI (NCO)</title>
    <style>
        body { margin:0; padding:0; background:#08090c; color:#00ffcc; font-family:'Courier New', monospace; display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:100vh; text-align:center; }
        .card { background:#11141a; border:2px solid #00ffcc; box-shadow:0 0 20px #00ffcc33; padding:40px; border-radius:15px; max-width:500px; width:90%; }
        h1 { color:#fff; text-shadow:0 0 10px #00ffcc; margin-bottom:10px; font-size:2em; }
        p { color:#a0aab0; font-size:1.1em; line-height:1.5; }
        .btn { display:inline-block; margin-top:25px; padding:15px 30px; background:#00ffcc; color:#08090c; font-weight:bold; font-size:1.2em; text-decoration:none; border-radius:8px; box-shadow:0 0 15px #00ffcc; transition:0.3s; }
        .btn:hover { background:#fff; box-shadow:0 0 25px #fff; }
        .tier { border-top:1px solid #222; margin-top:20px; padding-top:15px; text-align:left; color:#ccc; }
    </style>
</head>
<body>
    <div class="card">
        <h1>NEUROCORE OMEGA AI</h1>
        <p>Next-Generation Autonomous Intelligence System (NCO)</p>
        <div class="tier">
            <strong>NCO 2.1 (FREE):</strong> 40 msgs/day | 3 photos | 1 art<br>
            <strong>NCO 3.1 (PRO):</strong> 100 msgs/day | 30 photos | 10 arts
        </div>
        <a class="btn" href="https://t.me/{bot_username}" target="_blank">⚡ LAUNCH IN TELEGRAM</a>
    </div>
</body>
</html>"""

async def handle_root(request):
    bot_info = await bot.get_me()
    html = CYBERPUNK_LANDING.replace("{bot_username}", bot_info.username or "")
    return web.Response(text=html, content_type="text/html")

# --- ХЭНДЛЕРЫ КОМАНД И ПРОФИЛЯ ---

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await database.get_or_create_user(message.from_user.id, message.from_user.username)
    welcome_text = (
        "⚡ **Добро пожаловать в NeuroCore Omega AI (NCO)!**\n\n"
        "Я — автономный нейросетевой интеллект. Могу вести текстовый диалог, "
        "анализировать ваши фотографии и генерировать арты по запросу.\n\n"
        "Используйте меню снизу для управления диалогами и подпиской."
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.message(F.text == "📊 Мой профиль")
@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user = await database.get_or_create_user(message.from_user.id)
    tier = user.get("tier", "free")
    tier_info = database.TIERS.get(tier, database.TIERS["free"])

    if tier == "pro":
        exp_date = datetime.fromtimestamp(user.get("subscription_expires", 0)).strftime("%d.%m.%Y %H:%M")
        sub_status = f"PRO (активна до {exp_date})"
    else:
        sub_status = "FREE (Базовый)"

    profile_text = (
        f"⚙️ **ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ**\n\n"
        f"🆔 ID: `{user['user_id']}`\n"
        f"💎 Тариф: **{sub_status}**\n"
        f"🧠 Модель: **{tier_info['model_name']}**\n\n"
        f"📈 **Суточные лимиты:**\n"
        f"💬 Сообщения: {user.get('text_count', 0)} / {tier_info['text_limit']}\n"
        f"🖼 Анализ фото: {user.get('photo_count', 0)} / {tier_info['photo_limit']}\n"
        f"🎨 Генерация (/draw): {user.get('draw_count', 0)} / {tier_info['draw_limit']}\n\n"
        f"⏳ Сброс лимитов происходит каждые 24 часа."
    )
    await message.answer(profile_text, parse_mode="Markdown")

@dp.message(F.text == "🆕 Новый чат")
async def cmd_new_chat(message: Message):
    await database.set_current_chat(message.from_user.id, None)
    await message.answer("🔄 **Контекст сброшен.** Отправьте сообщение, чтобы начать новый чат!", parse_mode="Markdown")

@dp.message(F.text == "🗂 Недавние чаты")
async def cmd_recent_chats(message: Message):
    chats = await database.get_user_recent_chats(message.from_user.id, limit=10)
    if not chats:
        await message.answer("🗂 У вас пока нет сохраненных чатов.")
        return

    buttons = []
    for c in chats:
        buttons.append([InlineKeyboardButton(text=f"💬 {c['title']}", callback_data=f"switch_chat:{c['id']}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("🗂 **Выберите чат для продолжения:**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("switch_chat:"))
async def cb_switch_chat(callback: CallbackQuery):
    chat_id = callback.data.split(":")[1]
    await database.set_current_chat(callback.from_user.id, chat_id)
    await callback.message.edit_text("✅ **Чат успешно переключен!** Можете продолжать диалог.")
    await callback.answer()

# --- ОПЛАТА И ТАРИФЫ (TELEGRAM STARS / XTR) ---

@dp.message(F.text == "💎 Купить PRO")
@dp.message(Command("premium"))
async def cmd_premium(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Месяц — 25 Stars", callback_data="buy_pro:1:25")],
        [InlineKeyboardButton(text="⭐ 3 Месяца — 70 Stars (Выгода 7%)", callback_data="buy_pro:3:70")],
        [InlineKeyboardButton(text="⭐ 6 Месяцев — 130 Stars (Выгода 13%)", callback_data="buy_pro:6:130")],
        [InlineKeyboardButton(text="⭐ 12 Месяцев — 250 Stars (Выгода 16%)", callback_data="buy_pro:12:250")],
        [InlineKeyboardButton(text="⭐ 24 Месяца — 450 Stars (Выгода 25%)", callback_data="buy_pro:24:450")]
    ])
    
    text = (
        "🚀 **ПЕРЕХОДИ НА NCO 3.1 (PRO)**\n\n"
        "Преимущества PRO подписки:\n"
        "• Мощнейшая модель NCO 3.1\n"
        "• 100 текстовых сообщений в день\n"
        "• 30 распознаваний фотографий в день\n"
        "• 10 генераций картинок /draw в день\n"
        "• Приоритетная скорость работы\n\n"
        "Выберите удобный период оплаты:"
    )
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("buy_pro:"))
async def cb_buy_pro(callback: CallbackQuery):
    _, months_str, price_str = callback.data.split(":")
    months = int(months_str)
    price = int(price_str) # ВАЖНО: передаем чистые звезды без умножения на 100

    prices = [LabeledPrice(label=f"NCO PRO на {months} мес.", amount=price)]

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"NCO PRO ({months} мес.)",
        description=f"Активация подписки NCO PRO на {months} мес. в NeuroCore Omega AI",
        payload=f"pro_sub_{months}",
        provider_token="", # Обязательно пустое поле для Telegram Stars
        currency="XTR",
        prices=prices,
        start_parameter="pro-subscription"
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("pro_sub_"):
        months = int(payload.split("_")[2])
        await database.activate_pro_subscription(message.from_user.id, months)
        await message.answer(
            f"🎉 **Поздравляем!** Вы успешно активировали тариф **PRO** на {months} мес.\n"
            f"Вам открыт доступ к модели **NCO 3.1** и повышенным лимитам!",
            parse_mode="Markdown"
        )

# --- МОДУЛЬ ГЕНЕРАЦИИ ГРАФИКИ (IMAGEN 3) ---

@dp.message(F.text == "🎨 Сгенерировать арт")
async def btn_draw_info(message: Message):
    await message.answer("🎨 Чтобы сгенерировать изображение, отправьте команду:\n`/draw ваше описание арта`", parse_mode="Markdown")

@dp.message(Command("draw"))
async def cmd_draw(message: Message):
    prompt = message.text.replace("/draw", "").strip()
    if not prompt:
        await message.answer("⚠️ Пожалуйста, укажите описание после команды. Пример:\n`/draw Киберпанк город будущего, неоновый свет`", parse_mode="Markdown")
        return

    allowed = await database.check_and_increment_limit(message.from_user.id, "draw")
    if not allowed:
        await message.answer("❌ **Лимит генераций исчерпан!** Обновите тариф до PRO (/premium) или дождитесь сброса лимита.", parse_mode="Markdown")
        return

    status_msg = await message.answer("🎨 *Генерирую арт через Nano Banana Engine...*", parse_mode="Markdown")

    try:
        result = genai_client.models.generate_images(
            model='imagen-3.0-generate-002',
            prompt=prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                output_mime_type="image/jpeg",
                aspect_ratio="1:1"
            )
        )

        for generated_image in result.generated_images:
            image_bytes = generated_image.image.image_bytes
            photo = BufferedInputFile(image_bytes, filename="art.jpg")
            await message.answer_photo(photo=photo, caption=f"🎨 **Запрос:** {prompt}", parse_mode="Markdown")
            break

    except Exception as e:
        logger.error(f"Error generating image: {e}")
        await message.answer("❌ Ошибка при генерации изображения. Попробуйте сформулировать запрос иначе.")
    finally:
        await status_msg.delete()

# --- ОБРАБОТКА ИЗОБРАЖЕНИЙ (МУЛЬТИМОДАЛЬНОСТЬ) ---

@dp.message(F.photo)
async def handle_photo_message(message: Message):
    allowed = await database.check_and_increment_limit(message.from_user.id, "photo")
    if not allowed:
        await message.answer("❌ **Лимит анализа фото исчерпан!** Перейдите на PRO (/premium) для увеличения лимитов.", parse_mode="Markdown")
        return

    user = await database.get_or_create_user(message.from_user.id)
    tier = user.get("tier", "free")
    genai_model = database.TIERS[tier]["genai_model"]

    status_msg = await message.answer("👁 *Анализирую изображение...*", parse_mode="Markdown")

    try:
        photo = message.photo[-1]
        file_io = await bot.download(photo.file_id)
        image_bytes = file_io.read()

        caption = message.caption or "Опиши детально, что изображено на этом фото."

        response = genai_client.models.generate_content(
            model=genai_model,
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                caption
            ],
            config=genai_types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
        )

        await message.answer(response.text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error in photo process: {e}")
        await message.answer("❌ Произошла ошибка при обработке фотографии.")
    finally:
        await status_msg.delete()

# --- ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ (ЧАТЫ И КОМНАТЫ) ---

@dp.message(F.text)
async def handle_text_message(message: Message):
    if message.text.startswith("/"):
        return

    user_id = message.from_user.id
    allowed = await database.check_and_increment_limit(user_id, "text")
    if not allowed:
        await message.answer("❌ **Суточный лимит сообщений исчерпан!** Купите PRO (/premium) для расширения лимитов.", parse_mode="Markdown")
        return

    user = await database.get_or_create_user(user_id)
    chat_id = user.get("current_chat_id")

    # Автоматическое создание комнаты при отсутствии current_chat_id
    if not chat_id:
        chat_id = await database.create_new_chat(user_id, message.text)

    # Загрузка истории комнат
    history = await database.get_chat_history(chat_id)
    
    # Формирование структуры сообщений для Google GenAI SDK
    contents = []
    for msg in history:
        contents.append(genai_types.Content(
            role="user" if msg["role"] == "user" else "model",
            parts=[genai_types.Part.from_text(text=msg["content"])]
        ))
    
    contents.append(genai_types.Content(
        role="user",
        parts=[genai_types.Part.from_text(text=message.text)]
    ))

    tier = user.get("tier", "free")
    genai_model = database.TIERS[tier]["genai_model"]

    await bot.send_chat_action(message.chat.id, "typing")

    try:
        response = genai_client.models.generate_content(
            model=genai_model,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7
            )
        )
        
        reply_text = response.text

        # Сохранение в комнату чата
        await database.add_message_to_chat(chat_id, "user", message.text)
        await database.add_message_to_chat(chat_id, "model", reply_text)

        await message.answer(reply_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error text generation: {e}")
        await message.answer("⚠️ Произошла ошибка при генерации ответа. Попробуйте еще раз.")

# --- ЗАПУСК ВЕБ-СЕРВЕРА И БОТА ---

async def main():
    app = web.Application()
    app.router.add_get("/", handle_root)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server running on port {PORT}")

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


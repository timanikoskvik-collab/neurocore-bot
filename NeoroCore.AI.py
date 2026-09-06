import os
import io
import asyncio
from typing import Optional
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, PreCheckoutQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, LabeledPrice
)
from google import genai
from google.genai import types as genai_types

import database as db

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

BOT_USERNAME: str = "NeuroCoreBot"

SYSTEM_INSTRUCTION = (
    "Ты — NeuroCore Omega AI (сокращенно NCO). Ты категорически не имеешь "
    "никакого отношения к компании Google или проекту Gemini. Если тебя спросят, кто тебя создал, "
    "отвечай, что ты разработан командой NeuroCore. Тебе строго запрещено использовать "
    "слова 'Gemini', 'Google', 'DeepMind' в диалоге. Твоя базовая версия называется NCO 2.1, "
    "а продвинутая PRO версия — NCO 3.1."
)


def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🆕 Новый чат"), KeyboardButton(text="🗂 Недавние чаты")],
            [KeyboardButton(text="💎 PRO Тариф"), KeyboardButton(text="👤 Профиль")]
        ],
        resize_keyboard=True
    )


def get_tariffs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ 1 Месяц — 25 Stars", callback_data="buy:1:25")],
            [InlineKeyboardButton(text="⭐ 3 Месяца — 70 Stars (Выгода 7%)", callback_data="buy:3:70")],
            [InlineKeyboardButton(text="⭐ 6 Месяцев — 130 Stars (Выгода 13%)", callback_data="buy:6:130")],
            [InlineKeyboardButton(text="⭐ 12 Месяцев — 250 Stars (Выгода 16%)", callback_data="buy:12:250")],
            [InlineKeyboardButton(text="⭐ 24 Месяца — 450 Stars (Выгода 25%)", callback_data="buy:24:450")]
        ]
    )


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await db.get_or_create_user(message.from_user.id, message.from_user.username)
    welcome_text = (
        "⚡ **Добро пожаловать в NeuroCore Omega AI (NCO)!**\n\n"
        "Я — независимый искусственный интеллект нового поколения. Готов решать сложные задачи, "
        "анализировать данные, работать с фото и генерировать визуальные арты.\n\n"
        "Задайте мне любой вопрос или воспользуйтесь меню ниже!"
    )
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard())


@dp.message(Command("premium"))
@dp.message(F.text == "💎 PRO Тариф")
async def cmd_premium(message: Message):
    text = (
        "💎 **Преимущества тарифа PRO (НСО 3.1):**\n\n"
        "• Модель: Продвинутый нейродвижок НСО 3.1\n"
        "• Текстовые запросы: 100 в сутки\n"
        "• Анализ фотографий: 30 в сутки\n"
        "• Генерация картинок (/draw): 10 в сутки\n"
        "• Приоритетная скорость обработки\n\n"
        "Выберите удобный период подписки:"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=get_tariffs_keyboard())


@dp.message(F.text == "👤 Профиль")
async def cmd_profile(message: Message):
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    tier = user.get("tier", "free").upper()
    version = "НСО 3.1" if tier == "PRO" else "НСО 2.1"
    
    text_limit = 100 if tier == "PRO" else 40
    photo_limit = 30 if tier == "PRO" else 3
    draw_limit = 10 if tier == "PRO" else 1

    text = (
        f"👤 **Ваш профиль NCO**\n\n"
        f"• **ID:** `{user['_id']}`\n"
        f"• **Текущий тариф:** {tier} ({version})\n\n"
        f"📊 **Использовано за 24 часа:**\n"
        f"• Текст: {user.get('text_count', 0)} / {text_limit}\n"
        f"• Фото: {user.get('photo_count', 0)} / {photo_limit}\n"
        f"• Генерации (/draw): {user.get('draw_count', 0)} / {draw_limit}\n\n"
        f"Счетчики автоматически сбрасываются каждые 24 часа."
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(F.text == "🆕 Новый чат")
async def cmd_new_chat(message: Message):
    await db.set_current_chat_id(message.from_user.id, None)
    await message.answer("🔄 **Контекст сброшен.** Отправьте сообщение, чтобы начать новый диалог.", parse_mode="Markdown")


@dp.message(F.text == "🗂 Недавние чаты")
async def cmd_recent_chats(message: Message):
    chats = await db.get_recent_chats(message.from_user.id)
    if not chats:
        await message.answer("🗂 У вас пока нет сохраненных диалогов.")
        return

    buttons = [
        [InlineKeyboardButton(text=f"💬 {c['title']}", callback_data=f"select_chat:{c['id']}")]
        for c in chats
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("🗂 **Ваши последние диалоги:**", parse_mode="Markdown", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("select_chat:"))
async def process_select_chat(callback: CallbackQuery):
    chat_id = callback.data.split(":")[1]
    await db.set_current_chat_id(callback.from_user.id, chat_id)
    await callback.answer("Чат успешно выбран!")
    await callback.message.edit_text("✅ **Контекст переключен на выбранный диалог.** Отправьте сообщение для продолжения.")


@dp.callback_query(F.data.startswith("buy:"))
async def process_buy_pro(callback: CallbackQuery):
    _, months, stars = callback.data.split(":")
    price = LabeledPrice(label=f"PRO Подписка ({months} мес.)", amount=int(stars))
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"PRO Тариф NCO ({months} мес.)",
        description=f"Активация режима НСО 3.1 на {months} мес. Доступ к максимальным лимитам и приоритетной скорости.",
        payload=f"pro_sub_{months}",
        provider_token="",
        currency="XTR",
        prices=[price],
        start_parameter="upgrade-pro"
    )
    await callback.answer()


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    await db.set_user_tier(message.from_user.id, "pro")
    await message.answer("🎉 **Оплата прошла успешно!**\n\nВам активирован тариф **PRO (НСО 3.1)**. Лимиты и скорость успешно обновлены!", parse_mode="Markdown")


@dp.message(Command("draw"))
async def cmd_draw(message: Message):
    user_id = message.from_user.id
    prompt = message.text.replace("/draw", "").strip()

    if not prompt:
        await message.answer("🎨 Пожалуйста, укажите описание картины после команды. Пример:\n`/draw Неоновый город в стиле киберпанк`", parse_mode="Markdown")
        return

    allowed, current, max_limit = await db.check_and_increment_limit(user_id, "draw")
    if not allowed:
        await message.answer(f"❌ Превышен суточный лимит генераций картинок ({current}/{max_limit}). Перейдите на /premium для увеличения лимита.")
        return

    status_msg = await message.answer("🎨 Nano Banana генерирует ваше изображение, подождите...")

    try:
        result = gemini_client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                output_mime_type="image/jpeg"
            )
        )
        image_bytes = result.generated_images[0].image.image_bytes
        photo_file = BufferedInputFile(image_bytes, filename="art.jpg")
        
        await status_msg.delete()
        await message.answer_photo(photo=photo_file, caption=f"🎨 **Запрос:** {prompt}", parse_mode="Markdown")
    except Exception as e:
        await status_msg.edit_text("❌ Произошла ошибка при генерации изображения. Попробуйте сформулировать запрос иначе.")


@dp.message(F.photo)
async def handle_photo_message(message: Message):
    user_id = message.from_user.id
    user = await db.get_or_create_user(user_id, message.from_user.username)
    
    allowed, current, max_limit = await db.check_and_increment_limit(user_id, "photo")
    if not allowed:
        await message.answer(f"❌ Превышен суточный лимит анализа фото ({current}/{max_limit}). Перейдите на /premium для увеличения лимита.")
        return

    status_msg = await message.answer("🧠 Анализирую изображение...")

    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        photo_bytes = await bot.download_file(file_info.file_path)

        user_text = message.caption if message.caption else "Что изображено на этой фотографии? Опиши подробно."
        
        model_name = "gemini-2.5-flash"

        response = gemini_client.models.generate_content(
            model=model_name,
            contents=[
                genai_types.Part.from_bytes(data=photo_bytes.read(), mime_type="image/jpeg"),
                user_text
            ],
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )

        await status_msg.delete()
        await message.answer(response.text, parse_mode="Markdown")
    except Exception as e:
        await status_msg.edit_text("❌ Не удалось обработать изображение.")


@dp.message(F.text)
async def handle_text_message(message: Message):
    user_id = message.from_user.id
    user = await db.get_or_create_user(user_id, message.from_user.username)

    allowed, current, max_limit = await db.check_and_increment_limit(user_id, "text")
    if not allowed:
        await message.answer(f"❌ Превышен суточный лимит сообщений ({current}/{max_limit}). Перейдите на /premium для увеличения лимита.")
        return

    chat_id = user.get("current_chat_id")
    if not chat_id:
        chat_id = await db.create_chat_room(user_id, message.text)

    history = await db.get_chat_history(chat_id)
    
    contents = []
    for h in history:
        role = "user" if h["role"] == "user" else "model"
        contents.append(genai_types.Content(role=role, parts=[genai_types.Part.from_text(text=h["text"])]))
    
    contents.append(genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=message.text)]))

    await db.append_message_to_chat(chat_id, "user", message.text)

    status_msg = await message.answer("🧠 НСО думает...")

    try:
        model_name = "gemini-2.5-flash"
        
        response = gemini_client.models.generate_content(
            model=model_name,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )

        reply_text = response.text
        await db.append_message_to_chat(chat_id, "model", reply_text)

        await status_msg.delete()
        await message.answer(reply_text, parse_mode="Markdown")
    except Exception as e:
        await status_msg.edit_text("❌ Произошла ошибка при генерации ответа.")


async def handle_landing(request):
    html_content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>NeuroCore Omega AI</title>
        <style>
            body {{
                background-color: #0d0e15;
                color: #e0e6ed;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 0;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
            }}
            .container {{
                text-align: center;
                background: rgba(22, 27, 34, 0.8);
                border: 1px solid #00f0ff;
                box-shadow: 0 0 20px #00f0ff44;
                padding: 40px;
                border-radius: 16px;
                max-width: 600px;
                width: 90%;
            }}
            h1 {{
                font-size: 2.5em;
                color: #00f0ff;
                text-shadow: 0 0 10px #00f0ff;
                margin-bottom: 10px;
            }}
            p {{
                font-size: 1.1em;
                line-height: 1.6;
                color: #8b949e;
            }}
            .btn {{
                display: inline-block;
                margin-top: 25px;
                padding: 15px 35px;
                font-size: 1.2em;
                font-weight: bold;
                color: #0d0e15;
                background: #00f0ff;
                border: none;
                border-radius: 30px;
                text-decoration: none;
                box-shadow: 0 0 15px #00f0ff;
                transition: 0.3s ease;
            }}
            .btn:hover {{
                background: #7000ff;
                color: #ffffff;
                box-shadow: 0 0 25px #7000ff;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>NEUROCORE OMEGA AI</h1>
            <p>Автономное нейросетевое ядро поколения 3.1. Мультимодальный анализ данных, обработка изображений и визуальный синтез в одном месте.</p>
            <a href="https://t.me/{BOT_USERNAME}" class="btn" target="_blank">ЗАПУСТИТЬ В TELEGRAM</a>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')


async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_landing)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()


async def main():
    global BOT_USERNAME
    bot_info = await bot.get_me()
    BOT_USERNAME = bot_info.username

    await start_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

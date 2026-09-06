import os
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton
from google import genai
from google.genai import types as genai_types

import database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
genai_client = genai.Client(api_key=GEMINI_API_KEY)

# Оперативная память сессий для диалогов (без сохранения в БД)
user_chat_memories = {}

def get_main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🆕 Сбросить контекст"), KeyboardButton(text="⭐ PRO Подписка")],
            [KeyboardButton(text="🎨 Сгенерировать арт")]
        ],
        resize_keyboard=True
    )

async def handle_ping(request):
    return web.Response(text="<h1>NEUROCORE OMEGA AI - ONLINE</h1>", content_type='text/html')

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    await database.get_or_create_user(user_id, username)
    user_chat_memories[user_id] = []
    
    welcome_text = (
        "⚡ <b>Добро пожаловать в NeuroCore Omega AI!</b>\n\n"
        "• Текстовые модели: Gemini 3.5 / Gemini 3.7 PRO\n"
        "• Генерация картинок: Nano Banana\n"
        "• Распознавание отправленных фото"
    )
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_reply_keyboard())

@dp.message(Command("new"))
@dp.message(F.text == "🆕 Сбросить контекст")
async def process_new_chat(message: types.Message):
    user_id = message.from_user.id
    user_chat_memories[user_id] = []
    await message.answer("✨ <b>Контекст диалога очищен!</b>", parse_mode="HTML")

@dp.message(F.text == "⭐ PRO Подписка")
@dp.callback_query(F.data == "buy_pro")
async def process_buy_pro(event: types.Message | types.CallbackQuery):
    text = (
        "👑 <b>Преимущества NCO 3.1 PRO:</b>\n"
        "• Модель Gemini 3.7 Flash\n"
        "• 200 текстовых сообщений в день\n"
        "• 20 генераций картинок в день\n\n"
        "Выберите период подписки:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Месяц — 25 Stars", callback_data="buy_pro_1")],
        [InlineKeyboardButton(text="⭐ 3 Месяца — 70 Stars", callback_data="buy_pro_3")],
        [InlineKeyboardButton(text="⭐ 6 Месяцев — 130 Stars", callback_data="buy_pro_6")],
        [InlineKeyboardButton(text="⭐ 12 Месяцев — 240 Stars", callback_data="buy_pro_12")],
        [InlineKeyboardButton(text="⭐ 24 Месяца — 450 Stars", callback_data="buy_pro_24")]
    ])
    
    if isinstance(event, types.Message):
        await event.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await event.message.answer(text, parse_mode="HTML", reply_markup=kb)
        await event.answer()

@dp.callback_query(F.data.startswith("buy_pro_"))
async def process_buy_pro_callback(callback: types.CallbackQuery):
    months = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    await database.activate_pro_subscription(user_id, months)
    await callback.message.answer(f"🎉 Подписка PRO успешно активирована на {months} мес.!")
    await callback.answer()

@dp.message(F.text == "🎨 Сгенерировать арт")
async def btn_draw_info(message: types.Message):
    await message.answer("🎨 Чтобы сгенерировать арт через Nano Banana, отправьте команду `/draw` и описание.\n\n*Пример:* `/draw Космический корабль над планетой`", parse_mode="Markdown")

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    user_id = message.from_user.id
    prompt = message.text.replace("/draw", "").strip()

    if not prompt:
        await message.answer("⚠️ Укажите описание после `/draw`.", parse_mode="Markdown")
        return

    allowed = await database.check_and_increment_limit(user_id, "draw")
    if not allowed:
        await message.answer("❌ Достигнут суточный лимит генераций арт-контента.")
        return

    msg = await message.answer("🍌 **Nano Banana** генерирует изображение...")

    try:
        # Используем официальную модель Nano Banana (gemini-3.1-flash-image) для генерации
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-image",
            contents=[prompt],
        )
        
        image_bytes = None
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'inline_data') and part.inline_data:
                image_bytes = part.inline_data.data
                break
                
        if image_bytes:
            photo = BufferedInputFile(image_bytes, filename="nanobanana.png")
            await message.answer_photo(
                photo=photo,
                caption=f"🎨 **Nano Banana**\n📝 {prompt}",
                parse_mode="Markdown"
            )
            await msg.delete()
        else:
            await msg.edit_text("⚠️ Не удалось получить изображение. Попробуйте другой запрос.")
            
    except Exception as e:
        logger.error(f"Error drawing image: {e}")
        await msg.edit_text("⚠️ Ошибка генерации изображения через Nano Banana.")

# Обработка входящих фотографий (мультимодальность)
@dp.message(F.photo)
async def handle_photo_message(message: types.Message):
    user_id = message.from_user.id

    allowed = await database.check_and_increment_limit(user_id, "photo")
    if not allowed:
        await message.answer("❌ Исчерпан суточный лимит обработки фотографий.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    user = await database.get_or_create_user(user_id)
    tier = user.get("tier", "free")
    model_name = database.TIERS[tier]["genai_model"]

    try:
        # Скачиваем фото из Telegram
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        image_data = file_bytes.read()

        caption = message.caption or "Опиши подробно, что изображено на этой фотографии и ответь на вопросы по ней."

        response = genai_client.models.generate_content(
            model=model_name,
            contents=[
                caption,
                genai_types.Part.from_bytes(data=image_data, mime_type="image/jpeg")
            ]
        )
        await message.answer(response.text)
    except Exception as e:
        logger.error(f"Error processing photo: {e}")
        await message.answer("⚠️ Не удалось обработать отправленное изображение.")

# Обработка обычного текста с памятью текущего сеанса
@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    allowed = await database.check_and_increment_limit(user_id, "text")
    if not allowed:
        await message.answer("❌ Лимит текстовых сообщений на сегодня исчерпан.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    user = await database.get_or_create_user(user_id)
    tier = user.get("tier", "free")
    model_name = database.TIERS[tier]["genai_model"]

    if user_id not in user_chat_memories:
        user_chat_memories[user_id] = []

    # Добавляем в оперативную память сеанса
    user_chat_memories[user_id].append(f"Пользователь: {user_text}")

    # Формируем контекст (последние 10 сообщений)
    history_context = "\n".join(user_chat_memories[user_id][-10:])

    try:
        response = genai_client.models.generate_content(
            model=model_name,
            contents=history_context
        )
        reply_text = response.text
        
        user_chat_memories[user_id].append(f"Ассистент: {reply_text}")
        await message.answer(reply_text)
    except Exception as e:
        logger.error(f"Error text generation: {e}")
        await message.answer("⚠️ Ошибка генерации ответа. Попробуйте еще раз.")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    

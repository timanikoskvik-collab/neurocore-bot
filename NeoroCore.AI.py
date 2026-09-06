import os
import asyncio
import logging
import urllib.parse
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton
from google import genai
from google.genai import types as genai_types

import database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    logger.error("❌ Отсутствуют необходимые переменные окружения (TELEGRAM_TOKEN или GEMINI_API_KEY)!")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
genai_client = genai.Client(api_key=GEMINI_API_KEY)

# Оперативная память для удержания контекста диалога
user_chat_memories = {}

def get_main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⭐ PRO Подписка"), KeyboardButton(text="🎨 Сгенерировать арт")]
        ],
        resize_keyboard=True
    )

async def handle_ping(request):
    return web.Response(text="<h1>NEUROCORE OMEGA AI - ONLINE</h1>", content_type='text/html')

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username
        await database.get_or_create_user(user_id, username)
        user_chat_memories[user_id] = []
        
        welcome_text = (
            "⚡ <b>Добро пожаловать в NeuroCore Omega AI!</b>\n\n"
            "• Текстовые модели и работа с кодом: Gemini 3.5 / Gemini 3.7 PRO\n"
            "• Генерация графики: NeuroCore Vision 2.1\n"
            "• Распознавание отправленных фотографий"
        )
        await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_reply_keyboard())
    except Exception as e:
        logger.error(f"Error in cmd_start: {e}")

@dp.message(F.text == "⭐ PRO Подписка")
async def process_buy_pro(message: types.Message):
    text = (
        "👑 <b>Преимущества NCO 3.1 PRO:</b>\n"
        "• Модель Gemini 3.7 Flash\n"
        "• 200 текстовых сообщений в день\n"
        "• 20 генераций картинок в день\n\n"
        "Для активации обратитесь к администратору или используйте внутренние инструменты."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_reply_keyboard())

@dp.message(F.text == "🎨 Сгенерировать арт")
async def btn_draw_info(message: types.Message):
    await message.answer("🎨 Чтобы сгенерировать арт через **NeuroCore Vision 2.1**, отправьте команду `/draw` и описание.\n\n*Пример:* `/draw Космический корабль`", parse_mode="Markdown")

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    user_id = message.from_user.id
    prompt = message.text.replace("/draw", "").strip()

    if not prompt:
        await message.answer("⚠️ Укажите описание после `/draw`.", parse_mode="Markdown")
        return

    allowed = await database.check_and_increment_limit(user_id, "draw")
    if not allowed:
        await message.answer("❌ Достигнут суточный лимит генераций изображений.")
        return

    msg = await message.answer("🎨 **NeuroCore Vision 2.1** создает изображение...")

    try:
        encoded_prompt = urllib.parse.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&model=flux&nologo=true"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url, timeout=45) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                    photo = BufferedInputFile(image_data, filename="neuro_vision.png")
                    await message.answer_photo(
                        photo=photo,
                        caption=f"🎨 **NeuroCore Vision 2.1**\n📝 {prompt}",
                        parse_mode="Markdown"
                    )
                    await msg.delete()
                else:
                    await msg.edit_text("⚠️ Ошибка сервера генерации изображений. Попробуйте позже.")
    except asyncio.TimeoutError:
        await msg.edit_text("⚠️ Время ожидания генерации истекло. Попробуйте другой запрос.")
    except Exception as e:
        logger.error(f"Error drawing image: {e}")
        await msg.edit_text("⚠️ Не удалось получить изображение от NeuroCore Vision 2.1.")

@dp.message(F.photo)
async def handle_photo_message(message: types.Message):
    user_id = message.from_user.id

    allowed = await database.check_and_increment_limit(user_id, "photo")
    if not allowed:
        await message.answer("❌ Исчерпан суточный лимит обработки фотографий.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        user = await database.get_or_create_user(user_id)
        tier = user.get("tier", "free")
        model_name = database.TIERS[tier]["genai_model"]

        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        image_data = file_bytes.read()

        caption = message.caption or "Опиши подробно, что изображено на этой фотографии."

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
        await message.answer("⚠️ Не удалось распознать изображение из-за ошибки сервиса.")

@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    allowed = await database.check_and_increment_limit(user_id, "text")
    if not allowed:
        await message.answer("❌ Лимит текстовых сообщений на сегодня исчерпан.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        user = await database.get_or_create_user(user_id)
        tier = user.get("tier", "free")
        model_name = database.TIERS[tier]["genai_model"]

        if user_id not in user_chat_memories:
            user_chat_memories[user_id] = []

        user_chat_memories[user_id].append(f"Пользователь: {user_text}")
        history_context = "\n".join(user_chat_memories[user_id][-10:])

        response = genai_client.models.generate_content(
            model=model_name,
            contents=history_context
        )
        reply_text = response.text
        
        user_chat_memories[user_id].append(f"Ассистент: {reply_text}")
        await message.answer(reply_text)
    except Exception as e:
        logger.error(f"Error text generation: {e}")
        await message.answer("⚠️ Произошла ошибка при генерации ответа. Попробуйте еще раз.")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped!")
                         

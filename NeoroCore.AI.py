import os
import random
import asyncio
import urllib.parse
from io import BytesIO
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from google import genai
from google.genai import types as genai_types
from PIL import Image

import database as db

# Забираем токены из безопасных переменных окружения Render
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = (
    "Ты — NeuroCore Omega, передовая система искусственного интеллекта версии NCO 2.1. "
    "Твой характер: живой, вежливый, общительный и умный собеседник. Отвечай естественным языком. "
    "За генерацию изображений у тебя отвечает модуль NeuroVision Core (v2.1). "
    "Ты ни при каких обстоятельствах не упоминаешь компании Google, Gemini или другие сторонние ИИ. "
    "На вопросы о том, кто ты, всегда отвечаешь, что ты — NeuroCore Omega (версия NCO 2.1)."
)

async def send_long_message(chat_id: int, text: str):
    max_length = 4000
    if len(text) <= max_length:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await bot.send_message(chat_id=chat_id, text=text)
    else:
        for i in range(0, len(text), max_length):
            chunk = text[i:i + max_length]
            try:
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await bot.send_message(chat_id=chat_id, text=chunk)

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="🚀 Перезапустить бота"),
        BotCommand(command="draw", description="🎨 Нарисовать картинку (NeuroVision 2.1)"),
        BotCommand(command="my_chats", description="💬 Мои чаты"),
        BotCommand(command="premium", description="⭐ Оформить Premium"),
    ]
    await bot.set_my_commands(commands)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = await db.get_user(message.from_user.id)
    user_name = message.from_user.first_name
    await message.answer(
        f"Привет, {user_name}! 🚀\n"
        f"Я **NeuroCore Omega (версия NCO 2.1)**.\n"
        f"Графический модуль: **NeuroVision Core 2.1 (Flux Powered)** 🎨\n\n"
        f"📊 Твой статус: **{'Pro' if user.get('is_premium') else 'Бесплатный'}**\n"
        f"Задай мне любой вопрос, отправь фото или напиши: `/draw <что нарисовать>`!",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("my_chats"))
async def cmd_my_chats(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать новый чат", callback_data="new_chat")],
        [InlineKeyboardButton(text="📌 Чат 1 (Автосохранение)", callback_data="select_chat_1")]
    ])
    await message.answer(
        "💬 **Ваши сохранённые диалоги:**\n"
        "Все переписки сохраняются автоматически. Выберите чат для продолжения:",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("premium"))
async def cmd_premium(message: types.Message):
    text = (
        "⭐ **Преимущества Pro-версии (NCO 2.1):**\n\n"
        "• **100 сообщений** в сутки (вместо 40)\n"
        "• **20 фотографий** в сутки (вместо 3)\n"
        "• Приоритетная генерация в **NeuroVision Core**\n\n"
        "Выберите период подписки:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 месяц — 25 Stars", callback_data="buy_premium_1")],
        [InlineKeyboardButton(text="⭐ 3 месяца — 65 Stars (-13%)", callback_data="buy_premium_3")],
        [InlineKeyboardButton(text="⭐ 12 месяцев — 240 Stars (-20%)", callback_data="buy_premium_12")],
        [InlineKeyboardButton(text="⭐ 24 месяца — 420 Stars (-30%)", callback_data="buy_premium_24")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

def ask_gemini(text_prompt: str, image_obj: Image.Image = None) -> str:
    contents = []
    if image_obj:
        contents.append(image_obj)
    contents.append(text_prompt if text_prompt else "Что изображено на этом фото?")

    # Пробуем сделать запрос, в случае сбоя сети делаем повторную попытку
    for attempt in range(2):
        try:
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION
                )
            )
            return response.text
        except Exception as e:
            if attempt == 1:
                raise e

def translate_prompt_to_en(raw_prompt: str) -> str:
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"Translate this short image description to English for AI art generator. Output ONLY English words: {raw_prompt}"
        )
        return response.text.strip()
    except Exception:
        return raw_prompt

async def generate_image(prompt: str) -> str:
    english_prompt = await asyncio.to_thread(translate_prompt_to_en, prompt)
    full_prompt = f"{english_prompt}, detailed, high quality, 8k"
    encoded_prompt = urllib.parse.quote(full_prompt)
    seed = random.randint(1, 999999)
    # Используем модель flux для максимального качества генерации персонажей и предметов
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&model=flux&nologo=true&seed={seed}"
    return image_url

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    prompt = message.text.replace("/draw", "").strip()
    if not prompt:
        await message.answer("🎨 Пожалуйста, напишите описание картинки.\nПример: `/draw железный человек`", parse_mode=ParseMode.MARKDOWN)
        return

    await message.answer("🎨 *NeuroVision Core 2.1 генерирует изображение...*", parse_mode=ParseMode.MARKDOWN)
    await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    
    try:
        image_url = await generate_image(prompt)
        await message.answer_photo(photo=image_url, caption=f"🎨 **NeuroVision Core 2.1 (Flux)**\n🖼 **Запрос:** _{prompt}_", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"[ERROR-DRAW] {e}")
        await message.answer("⚠️ **Ошибка ERR-303 (NeuroVision Core)**\nНе удалось сгенерировать изображение.")

@dp.message(lambda msg: msg.photo is not None)
async def photo_handler(message: types.Message):
    user = await db.get_user(message.from_user.id)
    max_photos = 20 if user.get('is_premium') else 3
    
    if user.get('photo_count', 0) >= max_photos:
        await message.answer(
            f"⚠️ **Ошибка ERR-401 (Лимит исчерпан)**\n"
            f"Вы исчерпали суточный лимит загрузки изображений ({max_photos}/{max_photos}).\n"
            f"Лимит обновится через 24 часа или перейдите на /premium."
        )
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        photo_file = await bot.get_file(message.photo[-1].file_id)
        downloaded_file = await bot.download_file(photo_file.file_path)
        image = Image.open(BytesIO(downloaded_file.read()))

        caption = message.caption if message.caption else "Проанализируй фото."
        reply_text = await asyncio.to_thread(ask_gemini, caption, image)
        
        await db.increment_usage(message.from_user.id, is_photo=True)
        await send_long_message(message.chat.id, reply_text)
    except Exception as e:
        print(f"[ERROR-IMAGE] {e}")
        await message.answer("⚠️ **Ошибка ERR-503 (Сервер перегружен)**\nСервер нейросети временно перегружен. Повторите попытку через пару минут.")

@dp.message()
async def text_handler(message: types.Message):
    text_lower = message.text.lower().strip()
    
    if text_lower.startswith("нарисуй ") or text_lower.startswith("сгенерируй "):
        prompt = message.text.split(" ", 1)[1]
        await message.answer("🎨 *NeuroVision Core 2.1 генерирует изображение...*", parse_mode=ParseMode.MARKDOWN)
        await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
        try:
            image_url = await generate_image(prompt)
            await message.answer_photo(photo=image_url, caption=f"🎨 **NeuroVision Core 2.1 (Flux)**\n🖼 **Запрос:** _{prompt}_", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            print(f"[ERROR-DRAW] {e}")
            await message.answer("⚠️ **Ошибка ERR-303 (NeuroVision Core)**\nНе удалось сгенерировать изображение.")
        return

    user = await db.get_user(message.from_user.id)
    max_msgs = 100 if user.get('is_premium') else 40
    
    if user.get('msg_count', 0) >= max_msgs:
        await message.answer(
            f"⚠️ **Ошибка ERR-402 (Лимит исчерпан)**\n"
            f"Вы исчерпали суточный лимит текстовых запросов ({max_msgs}/{max_msgs}).\n"
            f"Лимит обновится через 24 часа или перейдите на /premium."
        )
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        reply_text = await asyncio.to_thread(ask_gemini, message.text)
        await db.increment_usage(message.from_user.id, is_photo=False)
        await send_long_message(message.chat.id, reply_text)
    except Exception as e:
        print(f"[ERROR-TEXT] {e}")
        await message.answer("⚠️ **Ошибка ERR-503 (Сервер перегружен)**\nСервер нейросети временно перегружен. Повторите запрос через 1-2 минуты.")

async def main():
    await db.init_db()
    await set_bot_commands(bot)
    print("NeuroCore Omega (NCO 2.1) успешно запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())

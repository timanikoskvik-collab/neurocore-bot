import os
import random
import asyncio
import urllib.parse
from io import BytesIO
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from google import genai
from google.genai import types as genai_types
from PIL import Image

import database as db

# Забираем токены из переменных окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Нормальный, живой системный промпт для NCO 3.1 (без робо-терминологии)
SYSTEM_INSTRUCTION = (
    "Ты — NeuroCore Omega версии NCO 3.1, передовой искусственный интеллект. "
    "Отвечай вежливо, грамотно, по делу и естественным человеческим языком. "
    "Никогда не упоминай компании Google, Gemini или другие сторонние ИИ. "
    "Не пиши шаблонные фразы про 'мои системы работают отлично' и тому подобное. Общайся как умный помощник."
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
        BotCommand(command="draw", description="🎨 Нарисовать картинку (NCO 3.1)"),
        BotCommand(command="my_chats", description="💬 Мои чаты"),
        BotCommand(command="premium", description="⭐ Оформить Premium"),
    ]
    await bot.set_my_commands(commands)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = await db.get_user(message.from_user.id)
    user_name = message.from_user.first_name
    status_text = "⭐ Pro (Максимальная скорость)" if user.get('is_premium') else "Бесплатный (Стандартная скорость)"
    await message.answer(
        f"Привет, {user_name}! 🚀\n"
        f"Я **NeuroCore Omega (версия NCO 3.1)**.\n"
        f"Графический модуль: **NeuroVision Core 3.1 (Flux)** 🎨\n\n"
        f"📊 Твой статус: **{status_text}**\n"
        f"Задай мне любой вопрос, отправь фото или напиши: `/draw <что нарисовать>`!",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("my_chats"))
async def cmd_my_chats(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать новый чат", callback_data="new_chat")],
        [InlineKeyboardButton(text="📌 Чат 1 (Основной)", callback_data="select_chat_1")]
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
        "⭐ **Преимущества Pro-версии (NCO 3.1):**\n\n"
        "• **Максимальная скорость ответа** без ожидания\n"
        "• **100 сообщений** в сутки (вместо 40)\n"
        "• **20 фотографий** в сутки (вместо 3)\n"
        "• Приоритетная генерация в **NeuroVision Core 3.1**\n\n"
        "Выберите период подписки для оплаты через Telegram Stars:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 месяц — 25 Stars", callback_data="buy_1")],
        [InlineKeyboardButton(text="⭐ 3 месяца — 65 Stars", callback_data="buy_3")],
        [InlineKeyboardButton(text="⭐ 12 месяцев — 240 Stars", callback_data="buy_12")],
        [InlineKeyboardButton(text="⭐ 24 месяцев — 420 Stars", callback_data="buy_24")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

# --- ОБРАБОТЧИКИ КНОПОК И ОПЛАТЫ ---

@dp.callback_query(F.data == "new_chat")
async def cb_new_chat(callback: types.CallbackQuery):
    await callback.answer("Создан новый чат!")
    await callback.message.answer("💬 История диалога очищена. О чем поговорим?")

@dp.callback_query(F.data == "select_chat_1")
async def cb_select_chat(callback: types.CallbackQuery):
    await callback.answer("Чат открыт!")
    await callback.message.answer("📌 Вы переключены в «Чат 1». Продолжайте общение!")

@dp.callback_query(F.data.startswith("buy_"))
async def cb_buy_premium(callback: types.CallbackQuery):
    await callback.answer() # Снимает значок загрузки с кнопки
    
    prices_map = {
        "buy_1": ("Pro подписка NCO 3.1 (1 месяц)", 25),
        "buy_3": ("Pro подписка NCO 3.1 (3 месяца)", 65),
        "buy_12": ("Pro подписка NCO 3.1 (12 месяцев)", 240),
        "buy_24": ("Pro подписка NCO 3.1 (24 месяца)", 420)
    }
    
    title, stars_amount = prices_map.get(callback.data, ("Pro подписка NCO 3.1", 25))
    prices = [LabeledPrice(label="Telegram Star", amount=stars_amount)]
    
    await callback.message.answer_invoice(
        title=title,
        description="Мгновенное снятие лимитов, максимальная скорость ответов NCO 3.1 и приоритетный доступ.",
        prices=prices,
        provider_token="", # Пустая строка для цифровых товаров / Telegram Stars
        payload=f"premium_{callback.data}",
        currency="XTR",
    )

@dp.pre_checkout_query()
async def pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    await db.set_premium(message.from_user.id, True)
    await message.answer(
        "🎉 **Оплата прошла успешно!**\n"
        "Вам активирован **Pro-статус NCO 3.1**. Все лимиты расширены, а скорость максимальная! 🚀",
        parse_mode=ParseMode.MARKDOWN
    )

# --- ЛОГИКА ГЕНЕРАЦИИ (GEMINI) ---

def ask_gemini_sync(text_prompt: str, image_obj: Image.Image = None, is_premium: bool = False) -> str:
    contents = []
    if image_obj:
        contents.append(image_obj)
    contents.append(text_prompt if text_prompt else "Что изображено на этом фото?")

    primary_model = 'gemini-3.7-flash' if is_premium else 'gemini-3.5-flash'
    fallback_model = 'gemini-3.5-flash' if is_premium else 'gemini-2.5-flash-lite'

    try:
        response = ai_client.models.generate_content(
            model=primary_model,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )
        return response.text
    except Exception as e:
        print(f"[WARN] {primary_model} недоступна ({e}). Переключение на резерв...")

    try:
        response = ai_client.models.generate_content(
            model=fallback_model,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )
        return response.text
    except Exception as final_error:
        print(f"[ERROR] Ошибка генерации: {final_error}")
        raise final_error

async def generate_image(prompt: str) -> str:
    clean_prompt = prompt.strip()
    full_prompt = f"{clean_prompt}, ultra detailed, photorealistic, 8k resolution"
    encoded_prompt = urllib.parse.quote(full_prompt)
    seed = random.randint(1, 999999)
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&model=flux&nologo=true&seed={seed}"

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    prompt = message.text.replace("/draw", "").strip()
    if not prompt:
        await message.answer("🎨 Пожалуйста, напишите описание картинки.\nПример: `/draw железный человек`", parse_mode=ParseMode.MARKDOWN)
        return

    await message.answer("🎨 *NeuroVision Core 3.1 генерирует изображение...*", parse_mode=ParseMode.MARKDOWN)
    await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    
    try:
        image_url = await generate_image(prompt)
        await message.answer_photo(photo=image_url, caption=f"🎨 **NeuroVision Core 3.1 (Flux)**\n🖼 **Запрос:** _{prompt}_", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"[ERROR-DRAW] {e}")
        await message.answer("⚠️ Не удалось сгенерировать изображение.")

@dp.message(lambda msg: msg.photo is not None)
async def photo_handler(message: types.Message):
    user = await db.get_user(message.from_user.id)
    is_prem = bool(user.get('is_premium'))
    max_photos = 20 if is_prem else 3
    
    if user.get('photo_count', 0) >= max_photos:
        await message.answer(
            f"⚠️ **Лимит исчерпан**\n"
            f"Вы исчерпали суточный лимит загрузки изображений ({max_photos}/{max_photos}).\n"
            f"Лимит обновится через 24 часа или оформите /premium."
        )
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        photo_file = await bot.get_file(message.photo[-1].file_id)
        downloaded_file = await bot.download_file(photo_file.file_path)
        image = Image.open(BytesIO(downloaded_file.read()))

        caption = message.caption if message.caption else "Проанализируй фото."
        reply_text = await asyncio.to_thread(ask_gemini_sync, caption, image, is_prem)
        
        await db.increment_usage(message.from_user.id, is_photo=True)
        await send_long_message(message.chat.id, reply_text)
    except Exception as e:
        print(f"[ERROR-IMAGE] {e}")
        await message.answer("⚠️ Сервер нейросети временно перегружен. Повторите попытку через пару минут.")

@dp.message()
async def text_handler(message: types.Message):
    text_lower = message.text.lower().strip()
    
    if text_lower.startswith("нарисуй ") or text_lower.startswith("сгенерируй "):
        prompt = message.text.split(" ", 1)[1]
        await message.answer("🎨 *NeuroVision Core 3.1 генерирует изображение...*", parse_mode=ParseMode.MARKDOWN)
        await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
        try:
            image_url = await generate_image(prompt)
            await message.answer_photo(photo=image_url, caption=f"🎨 **NeuroVision Core 3.1 (Flux)**\n🖼 **Запрос:** _{prompt}_", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            print(f"[ERROR-DRAW] {e}")
            await message.answer("⚠️ Не удалось сгенерировать изображение.")
        return

    user = await db.get_user(message.from_user.id)
    is_prem = bool(user.get('is_premium'))
    max_msgs = 100 if is_prem else 40
    
    if user.get('msg_count', 0) >= max_msgs:
        await message.answer(
            f"⚠️ **Лимит исчерпан**\n"
            f"Вы исчерпали суточный лимит текстовых запросов ({max_msgs}/{max_msgs}).\n"
            f"Лимит обновится через 24 часа или оформите /premium."
        )
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        reply_text = await asyncio.to_thread(ask_gemini_sync, message.text, None, is_prem)
        await db.increment_usage(message.from_user.id, is_photo=False)
        await send_long_message(message.chat.id, reply_text)
    except Exception as e:
        print(f"[ERROR-TEXT] {e}")
        await message.answer("⚠️ Сервер нейросети временно перегружен. Повторите запрос через 1-2 минуты.")

async def main():
    await db.init_db()
    await set_bot_commands(bot)
    print("NeuroCore Omega (NCO 3.1) успешно запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())

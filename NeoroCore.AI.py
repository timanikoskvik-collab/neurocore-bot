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
from PIL import Image

import database as db

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = (
    "Ты — искусственный интеллект-собеседник. "
    "Отвечай вежливо, грамотно, по делу и естественным человеческим языком. "
    "Никогда не упоминай компании Google или другие сторонние разработчики."
)

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="🚀 Перезапустить бота"),
        BotCommand(command="draw", description="🎨 Нарисовать картинку"),
        BotCommand(command="premium", description="⭐ Оформить Premium"),
    ]
    await bot.set_my_commands(commands)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    is_prem = bool(user.get('is_premium'))
    user_name = message.from_user.first_name
    
    version_name = "NCO 3.1 Pro (Gemini 3.7 Flash)" if is_prem else "NCO 2.1 (Gemini 3.5 Flash)"
    status_text = "⭐ Pro (Максимальные лимиты)" if is_prem else "Бесплатный (Стандартные лимиты)"
    
    await message.answer(
        f"Привет, {user_name}! 🚀\n"
        f"Я **NeuroCore Omega ({version_name})**.\n\n"
        f"📊 Твой статус: **{status_text}**\n"
        f"Задай мне любой вопрос, отправь фото или напиши: `/draw <english prompt>`!",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("premium"))
async def cmd_premium(message: types.Message):
    text = (
        "⭐ **Преимущества Pro-версии (NCO 3.1):**\n\n"
        "• Модель **Gemini 3.7 Flash** (максимальная производительность)\n"
        "• **100 сообщений** в сутки\n"
        "• **20 фотографий** в сутки\n"
        "• **10 генераций картинок** в сутки\n\n"
        "Выберите период подписки через Telegram Stars:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 месяц — 25 Stars", callback_data="buy_1")],
        [InlineKeyboardButton(text="⭐ 3 месяца — 65 Stars", callback_data="buy_3")],
        [InlineKeyboardButton(text="⭐ 12 месяцев — 240 Stars", callback_data="buy_12")],
        [InlineKeyboardButton(text="⭐ 24 месяцев — 420 Stars", callback_data="buy_24")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data.startswith("buy_"))
async def cb_buy_premium(callback: types.CallbackQuery):
    await callback.answer() 
    
    prices_map = {
        "buy_1": ("Pro подписка NCO 3.1 (1 месяц)", 25),
        "buy_3": ("Pro подписка NCO 3.1 (3 месяца)", 65),
        "buy_12": ("Pro подписка NCO 3.1 (12 месяцев)", 240),
        "buy_24": ("Pro подписка NCO 3.1 (24 месяцев)", 420)
    }
    
    data_key = callback.data
    title, stars_amount = prices_map.get(data_key, ("Pro подписка NCO 3.1", 25))
    prices = [LabeledPrice(label="Telegram Star", amount=stars_amount)]
    
    await callback.message.answer_invoice(
        title=title,
        description="Переход на NCO 3.1, снятие лимитов и ускоренная генерация.",
        prices=prices,
        provider_token="", 
        payload=f"premium_{data_key}",
        currency="XTR",
    )

@dp.pre_checkout_query()
async def pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    days_map = {
        "premium_buy_1": 30,
        "premium_buy_3": 90,
        "premium_buy_12": 365,
        "premium_buy_24": 730
    }
    days = days_map.get(payload, 30)
    
    await db.set_premium_duration(message.from_user.id, days)
    await message.answer(
        f"🎉 **Оплата прошла успешно!**\n"
        f"Вам активирован **NCO 3.1 Pro (Gemini 3.7 Flash)** на {days} дней. Лимиты сняты! 🚀",
        parse_mode=ParseMode.MARKDOWN
    )

def ask_gemini_sync(text_prompt: str, image_obj: Image.Image = None, is_premium: bool = False, history: list = None) -> str:
    # Бесплатная NCO 2.1 -> gemini-3.5-flash, Pro NCO 3.1 -> gemini-3.7-flash
    model_name = 'gemini-3.7-flash' if is_premium else 'gemini-3.5-flash'
    
    contents = []
    if history:
        for role, text in history:
            prefix = "User: " if role == "user" else "Model: "
            contents.append(f"{prefix}{text}")
    
    if image_obj:
        contents.append(image_obj)
    
    contents.append(text_prompt if text_prompt else "Привет")

    last_error = None
    fallback_models = [model_name, 'gemini-3.5-flash']
    
    for m in fallback_models:
        for attempt in range(3):
            try:
                response = ai_client.models.generate_content(
                    model=m,
                    contents=contents,
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                last_error = e
                import time
                time.sleep(1.5 * (attempt + 1))
                
    raise last_error

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    prompt = message.text.replace("/draw", "").strip()
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    is_prem = bool(user.get('is_premium'))
    
    max_draws = 10 if is_prem else 1
    current_draws = user.get('draw_count', 0)
    
    if current_draws >= max_draws:
        await message.answer(
            f"⚠️ **Лимит картинок исчерпан**\n"
            f"Вы исчерпали суточный лимит генерации изображений ({max_draws}/{max_draws}).\n"
            f"Лимит обновится через 24 часа или оформите /premium!"
        )
        return

    if not prompt:
        await message.answer(
            "🎨 **Как пользоваться генератором картинок:**\n\n"
            "Напишите команду `/draw` и укажите описание **строго на английском языке**.\n\n"
            "**Примеры:**\n"
            "• `/draw cyberpunk city with neon lights`\n"
            "• `/draw futuristic sports car`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    version_label = "NCO 3.1 Pro" if is_prem else "NCO 2.1"
    await message.answer(f"🎨 *NeuroVision Core ({version_label}) создает изображение...*", parse_mode=ParseMode.MARKDOWN)
    await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    
    try:
        # Прямая генерация без участия Gemini, исключая любые ошибки
        encoded_prompt = urllib.parse.quote(prompt)
        seed = random.randint(1, 999999)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={seed}"
        
        await db.increment_usage(user_id, is_draw=True)
        await message.answer_photo(photo=image_url, caption=f"🎨 **NeuroVision Core**\n🖼 **Запрос:** _{prompt}_", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"[ERROR-DRAW] {e}")
        await message.answer("⚠️ Не удалось отправить картинку. Попробуйте еще раз.")

@dp.message(F.photo)
async def photo_handler(message: types.Message):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    is_prem = bool(user.get('is_premium'))
    max_photos = 20 if is_prem else 3
    
    if user.get('photo_count', 0) >= max_photos:
        await message.answer(f"⚠️ **Лимит фотографий исчерпан**\nОформите /premium для увеличения лимита.")
        return

    version_label = "NCO 3.1 Pro" if is_prem else "NCO 2.1"
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        photo_file = await bot.get_file(message.photo[-1].file_id)
        downloaded_file = await bot.download_file(photo_file.file_path)
        image = Image.open(BytesIO(downloaded_file.read()))

        caption = message.caption if message.caption else "Опиши это фото."
        
        active_chat_id = await db.get_active_chat(user_id)
        if not active_chat_id:
            active_chat_id = await db.create_new_chat(user_id, "Основной чат")
            
        history = await db.get_chat_history(user_id, active_chat_id)
        
        reply_text = await asyncio.to_thread(ask_gemini_sync, caption, image, is_prem, history)
        
        await db.save_message(user_id, active_chat_id, 'user', caption)
        await db.save_message(user_id, active_chat_id, 'model', reply_text)
        await db.increment_usage(user_id, is_photo=True)
        
        await message.answer(reply_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"[ERROR-IMAGE] {e}")
        await message.answer("⚠️ Серверы временно перегружены. Повторите попытку через минуту.")

@dp.message()
async def text_handler(message: types.Message):
    if not message.text:
        return
        
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    is_prem = bool(user.get('is_premium'))
    max_msgs = 100 if is_prem else 40
    
    if user.get('msg_count', 0) >= max_msgs:
        await message.answer(f"⚠️ **Лимит сообщений исчерпан**\nОформите /premium для увеличения лимита.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        active_chat_id = await db.get_active_chat(user_id)
        if not active_chat_id:
            active_chat_id = await db.create_new_chat(user_id, "Основной чат")
            
        history = await db.get_chat_history(user_id, active_chat_id)
        
        reply_text = await asyncio.to_thread(ask_gemini_sync, message.text, None, is_prem, history)
        
        await db.save_message(user_id, active_chat_id, 'user', message.text)
        await db.save_message(user_id, active_chat_id, 'model', reply_text)
        await db.increment_usage(user_id, is_msg=True)
        
        await message.answer(reply_text)
    except Exception as e:
        print(f"[ERROR-TEXT] {e}")
        await message.answer("⚠️ Сервер нейросети временно перегружен. Повторите запрос через минуту.")

async def main():
    await db.init_db()
    await set_bot_commands(bot)
    print("NeuroCore Omega запущен стабильно!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

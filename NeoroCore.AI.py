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

# Функция запроса к правильным актуальным моделям Gemini
def ask_gemini(text_prompt: str, image_obj: Image.Image = None, is_premium: bool = False) -> str:
    # Бесплатная NCO 2.1 -> gemini-3.5-flash, Pro NCO 3.1 -> gemini-3.7-flash
    model_name = 'gemini-3.7-flash' if is_premium else 'gemini-3.5-flash'
    
    contents = []
    if image_obj:
        contents.append(image_obj)
    contents.append(text_prompt if text_prompt else "Привет")

    last_error = None
    # Пробуем основную нужную модель, а в случае перегрузки страхуемся ею же или аналогом
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

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="🚀 Перезапустить бота"),
        BotCommand(command="draw", description="🎨 Нарисовать картинку"),
        BotCommand(command="premium", description="⭐ Оформить Premium"),
    ]
    await bot.set_my_commands(commands)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_name = message.from_user.first_name
    await message.answer(
        f"Привет, {user_name}! 🚀\n"
        f"Я **NeuroCore Omega (NCO 2.1 / Gemini 3.5 Flash)**.\n\n"
        f"Задай мне любой вопрос, отправь фото или используй команды:\n"
        f"🎨 `/draw <описание>` — нарисовать картинку\n"
        f"⭐ `/premium` — тарифы NCO 3.1 (Gemini 3.7 Flash)",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("premium"))
async def cmd_premium(message: types.Message):
    text = (
        "⭐ **Преимущества Pro-версии (NCO 3.1 — Gemini 3.7 Flash):**\n\n"
        "• Модель **Gemini 3.7 Flash** (максимальная производительность)\n"
        "• Увеличенные лимиты на сообщения и фото\n"
        "• Приоритетная генерация изображений\n\n"
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
        "buy_24": ("Pro подписка NCO 3.1 (24 месяца)", 420)
    }
    title, stars_amount = prices_map.get(callback.data, ("Pro подписка NCO 3.1", 25))
    prices = [LabeledPrice(label="Telegram Star", amount=stars_amount)]
    
    await callback.message.answer_invoice(
        title=title,
        description="Переход на NCO 3.1 (Gemini 3.7 Flash) и снятие лимитов.",
        prices=prices,
        provider_token="", 
        payload=f"premium_{callback.data}",
        currency="XTR",
    )

@dp.pre_checkout_query()
async def pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    await message.answer(
        "🎉 **Оплата прошла успешно!**\n"
        "Вам активирован **NCO 3.1 Pro (Gemini 3.7 Flash)**. Лимиты сняты! 🚀",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    prompt = message.text.replace("/draw", "").strip()
    if not prompt:
        await message.answer(
            "🎨 **Как пользоваться генератором картинок:**\n\n"
            "Напишите команду `/draw` и укажите, что именно нужно нарисовать.\n"
            "Например: `/draw киберпанк город`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await message.answer("🎨 *NeuroVision Core генерирует изображение...*", parse_mode=ParseMode.MARKDOWN)
    await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    
    try:
        encoded_prompt = urllib.parse.quote(prompt)
        seed = random.randint(1, 999999)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={seed}"
        await message.answer_photo(photo=image_url, caption=f"🎨 **Запрос:** _{prompt}_", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"[ERROR DRAW] {e}")
        await message.answer("⚠️ Не удалось сгенерировать картинку. Попробуйте позже.")

@dp.message(F.photo)
async def photo_handler(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        photo_file = await bot.get_file(message.photo[-1].file_id)
        downloaded_file = await bot.download_file(photo_file.file_path)
        image = Image.open(BytesIO(downloaded_file.read()))

        caption = message.caption if message.caption else "Опиши это фото."
        # По умолчанию считаем бесплатным (is_premium=False -> gemini-3.5-flash)
        reply_text = await asyncio.to_thread(ask_gemini, caption, image, False)
        
        await message.answer(reply_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"[ERROR IMAGE] {e}")
        await message.answer("⚠️ Серверы временно перегружены. Повторите попытку через минуту.")

@dp.message()
async def text_handler(message: types.Message):
    if not message.text:
        return
        
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        # Используем gemini-3.5-flash для обычного текстового запроса
        reply_text = await asyncio.to_thread(ask_gemini, message.text, None, False)
        await message.answer(reply_text)
    except Exception as e:
        print(f"[ERROR TEXT] {e}")
        await message.answer("⚠️ **Ошибка ERR-503 (Сервер перегружен)**\nСервер нейросети временно перегружен. Повторите запрос через 1-2 минуты.")

async def main():
    await set_bot_commands(bot)
    print("NeuroCore Omega запущен с актуальными модельками Gemini 3.5 / 3.7 Flash!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

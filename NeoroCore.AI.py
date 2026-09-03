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

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Умный системный промпт без упоминаний серверов и шаблонов
SYSTEM_INSTRUCTION = (
    "Ты — высокоинтеллектуальный, эрудированный и живой собеседник NeuroCore Omega. "
    "Отвечай глубоко, точными формулировками, проявляй эмпатию и интеллект. "
    "Никогда не используй фразы вроде 'мои сервера работают', 'я искусственный интеллект', 'я модель'. "
    "Никогда не упоминай компанию Google или сторонних разработчиков. "
    "Ты отлично помнишь контекст текущего диалога и ведешь разговор максимально естественно."
)

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="🚀 Главное меню"),
        BotCommand(command="draw", description="🎨 Сгенерировать картинку"),
        BotCommand(command="premium", description="⭐ Оформить Pro подписку"),
        BotCommand(command="clear", description="🧹 Очистить память диалога"),
    ]
    await bot.set_my_commands(commands)

async def ask_gemini(text_prompt: str, image_obj: Image.Image = None, is_premium: bool = False, history: list = None) -> str:
    # NCO 2.1 (Free) -> gemini-3.5-flash | NCO 3.1 (Pro) -> gemini-3.7-flash
    model_name = 'gemini-3.7-flash' if is_premium else 'gemini-3.5-flash'
    
    contents = []
    
    if history:
        for msg in history:
            api_role = "user" if msg['role'] == "user" else "model"
            contents.append(
                genai_types.Content(
                    role=api_role,
                    parts=[genai_types.Part.from_text(text=msg['parts'][0]['text'])]
                )
            )
            
    current_parts = []
    if image_obj:
        img_byte_arr = BytesIO()
        image_obj.save(img_byte_arr, format='JPEG')
        current_parts.append(
            genai_types.Part.from_bytes(
                data=img_byte_arr.getvalue(),
                mime_type='image/jpeg'
            )
        )
    
    if text_prompt:
        current_parts.append(genai_types.Part.from_text(text=text_prompt))
        
    contents.append(genai_types.Content(role="user", parts=current_parts))

    config = genai_types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.75
    )

    response = await ai_client.aio.models.generate_content(
        model=model_name,
        contents=contents,
        config=config
    )
    
    if response and response.text:
        return response.text
    else:
        raise ValueError("Ошибка получения ответа от модели")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    is_prem = bool(user.get('is_premium'))
    user_name = message.from_user.first_name
    
    version_name = "NCO 3.1 Pro (Gemini 3.7 Flash)" if is_prem else "NCO 2.1 (Gemini 3.5 Flash)"
    status_text = "⭐ Pro-доступ" if is_prem else "Бесплатный доступ"
    
    await message.answer(
        f"Приветствую, {user_name}! 🚀\n"
        f"Я **NeuroCore Omega ({version_name})**.\n\n"
        f"📊 Ваш статус: **{status_text}**\n"
        f"✉️ Сообщений доступно: **{user['msg_left']}**\n"
        f"📸 Фотографий доступно: **{user['photo_left']}**\n"
        f"🎨 Генераций картинок: **{user['draw_left']}**\n\n"
        f"Задайте любой вопрос, отправьте фото или напишите `/draw <запрос>`.",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    await db.clear_history(message.from_user.id)
    await message.answer("🧹 Память диалога сброшена! Начинаем с чистого листа.")

@dp.message(Command("premium"))
async def cmd_premium(message: types.Message):
    text = (
        "⭐ **Преимущества NCO 3.1 Pro:**\n\n"
        "• Модель **Gemini 3.7 Flash**\n"
        "• **100 сообщений** в сутки\n"
        "• **20 фотографий** в сутки\n"
        "• **10 генераций картинок** в сутки\n\n"
        "Выберите период оплаты через Telegram Stars:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 месяц — 25 Stars", callback_data="buy_1")],
        [InlineKeyboardButton(text="⭐ 12 месяцев — 240 Stars", callback_data="buy_12")],
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data.startswith("buy_"))
async def cb_buy_premium(callback: types.CallbackQuery):
    await callback.answer() 
    data_key = callback.data
    prices_map = {
        "buy_1": ("Pro подписка NCO 3.1 (1 месяц)", 25),
        "buy_12": ("Pro подписка NCO 3.1 (12 месяцев)", 240),
    }
    
    title, stars_amount = prices_map.get(data_key, ("Pro подписка NCO 3.1", 25))
    prices = [LabeledPrice(label="Telegram Star", amount=stars_amount)]
    
    await callback.message.answer_invoice(
        title=title,
        description="Активация NCO 3.1 Pro, снятие ограничений.",
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
    await db.set_premium(message.from_user.id, True)
    await message.answer("🎉 **Премиум успешно активирован!** Добро пожаловать в NCO 3.1 Pro.", parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    
    if user['draw_left'] <= 0:
        await message.answer("⚠️ **Суточный лимит генераций исчерпан.** Зайдите через час за бонусом или дождитесь сброса через 24 часа!")
        return

    prompt = message.text.replace("/draw", "").strip()
    if not prompt:
        await message.answer("🎨 Укажите описание. Пример: `/draw футуристичный город`", parse_mode=ParseMode.MARKDOWN)
        return

    version_label = "NCO 3.1 Pro" if user.get('is_premium') else "NCO 2.1"
    await message.answer(f"🎨 *NeuroVision ({version_label}) создает изображение...*", parse_mode=ParseMode.MARKDOWN)
    await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    
    try:
        is_prem = bool(user.get('is_premium'))
        translated_prompt = await ask_gemini(f"Translate this text prompt to English for image generator. Return ONLY translation: {prompt}", None, is_prem)
        
        encoded_prompt = urllib.parse.quote(translated_prompt.strip())
        seed = random.randint(1, 999999)
        image_url = f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&seed={seed}&model=flux"
        
        await message.answer_photo(photo=image_url, caption=f"🎨 **NeuroVision Core**\n🖼 **Запрос:** _{prompt}_", parse_mode=ParseMode.MARKDOWN)
        await db.decrement_limit(user_id, 'draw')
        
    except Exception as e:
        print(f"[ERROR-DRAW] {e}")
        await message.answer("⚠️ Не удалось сгенерировать изображение. Ваш лимит не списан.")

@dp.message(F.photo)
async def photo_handler(message: types.Message):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    
    if user['photo_left'] <= 0:
        await message.answer("⚠️ **Лимит фотографий исчерпан.** Дождитесь обновления или зайдите через час.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        photo_file = await bot.get_file(message.photo[-1].file_id)
        downloaded_file = await bot.download_file(photo_file.file_path)
        image = Image.open(BytesIO(downloaded_file.read()))

        caption = message.caption if message.caption else "Подробно опиши, что изображено на фото."
        
        history = await db.get_history(user_id)
        is_prem = bool(user.get('is_premium'))
        
        reply_text = await ask_gemini(caption, image, is_prem, history)
        
        await message.answer(reply_text)
        await db.add_message(user_id, 'user', caption)
        await db.add_message(user_id, 'model', reply_text)
        await db.decrement_limit(user_id, 'photo')
        
    except Exception as e:
        print(f"[ERROR-IMAGE] {e}")
        await message.answer("⚠️ Ошибка обработки фото. Лимит не списан.")

@dp.message(F.text)
async def text_handler(message: types.Message):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    
    if user['msg_left'] <= 0:
        await message.answer("⚠️ **Лимит сообщений исчерпан.** Дождитесь обновления или зайдите через час за бонусным лимитом.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        history = await db.get_history(user_id)
        is_prem = bool(user.get('is_premium'))
        
        reply_text = await ask_gemini(message.text, None, is_prem, history)
        
        await message.answer(reply_text)
        await db.add_message(user_id, 'user', message.text)
        await db.add_message(user_id, 'model', reply_text)
        await db.decrement_limit(user_id, 'msg')
        
    except Exception as e:
        print(f"[ERROR-TEXT] {e}")
        await message.answer("⚠️ Сбой генерации. Ваш лимит не списан, попробуйте еще раз.")

async def main():
    await db.init_db()
    await set_bot_commands(bot)
    print("NeuroCore Omega запущен упешно!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

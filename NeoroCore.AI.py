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

SYSTEM_INSTRUCTION = (
    "Ты — искусственный интеллект-собеседник. "
    "Отвечай вежливо, грамотно, по делу и естественным человеческим языком. "
    "Никогда не упоминай компании Google или другие сторонние разработчики. "
    "Не пиши шаблонные фразы про 'мои системы работают отлично'. Общайся как умный помощник."
)

def format_error_code(err_type: str, exception: Exception) -> str:
    err_hash = abs(hash(str(exception))) % 10000
    return f"⚠️ [NCO-{err_type} | REF: {err_hash:04d}] Системный узел временно недоступен. Повторите запрос."

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
        BotCommand(command="draw", description="🎨 Нарисовать картинку"),
        BotCommand(command="my_chats", description="💬 Мои чаты"),
        BotCommand(command="premium", description="⭐ Оформить Premium"),
    ]
    await bot.set_my_commands(commands)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = await db.get_user(message.from_user.id)
    is_prem = bool(user.get('is_premium'))
    user_name = message.from_user.first_name
    
    version_name = "NCO 3.1 (Pro)" if is_prem else "NCO 2.1 (Бесплатный)"
    status_text = "⭐ Pro (Максимальная скорость)" if is_prem else "Бесплатный (Стандартная скорость)"
    
    await message.answer(
        f"Привет, {user_name}! 🚀\n"
        f"Я **NeuroCore Omega (версия {version_name})**.\n"
        f"Графический модуль: **NeuroVision Core (Flux)** 🎨\n\n"
        f"📊 Твой статус: **{status_text}**\n"
        f"Задай мне любой вопрос, отправь фото или напиши: `/draw <что нарисовать>`!",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("my_chats"))
async def cmd_my_chats(message: types.Message):
    user_id = message.from_user.id
    chats = await db.get_user_chats(user_id)
    
    keyboard_buttons = [
        [InlineKeyboardButton(text="➕ Создать новый чат", callback_data="new_chat")]
    ]
    
    for chat_id, title in chats:
        keyboard_buttons.append([
            InlineKeyboardButton(text=f"📌 {title}", callback_data=f"select_chat_{chat_id}")
        ])
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
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
        "• Работа на флагманской модели (максимальная скорость)\n"
        "• **100 сообщений** в сутки (вместо 40)\n"
        "• **20 фотографий** в сутки (вместо 3)\n"
        "• Приоритетная генерация картинок\n\n"
        "Выберите период подписки для оплаты через Telegram Stars:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 месяц — 25 Stars", callback_data="buy_1")],
        [InlineKeyboardButton(text="⭐ 3 месяца — 65 Stars", callback_data="buy_3")],
        [InlineKeyboardButton(text="⭐ 12 месяцев — 240 Stars", callback_data="buy_12")],
        [InlineKeyboardButton(text="⭐ 24 месяцев — 420 Stars", callback_data="buy_24")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data == "new_chat")
async def cb_new_chat(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    new_chat_id = await db.create_new_chat(user_id, "Новый сеанс")
    await callback.answer("Создан новый чат!")
    await callback.message.answer(f"💬 Создан чат #{new_chat_id}. История диалога очищена. О чем поговорим?")

@dp.callback_query(F.data.startswith("select_chat_"))
async def cb_select_chat(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    chat_id = int(callback.data.split("_")[-1])
    await db.set_active_chat(user_id, chat_id)
    await callback.answer("Чат открыт!")
    await callback.message.answer(f"📌 Вы переключены в сеанс #{chat_id}. Продолжайте общение!")

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
        description="Переход на NCO 3.1, снятие лимитов и ускоренная генерация.",
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
    await db.set_premium(message.from_user.id, True)
    await message.answer(
        "🎉 **Оплата прошла успешно!**\n"
        "Вам активирован **NCO 3.1 (Pro)**. Скорость и лимиты максимальные! 🚀",
        parse_mode=ParseMode.MARKDOWN
    )

def ask_gemini_sync(text_prompt: str, image_obj: Image.Image = None, is_premium: bool = False, history: list = None) -> str:
    contents = []
    
    if history:
        for role, text in history:
            api_role = "user" if role == "user" else "model"
            contents.append({
                "role": api_role,
                "parts": [{"text": text}]
            })
    
    current_parts = []
    if image_obj:
        current_parts.append(image_obj)
    current_parts.append(text_prompt if text_prompt else "Что изображено на этом фото?")
    
    contents.append({
        "role": "user",
        "parts": current_parts
    })

    primary_model = 'gemini-2.5-flash'
    fallback_model = 'gemini-2.5-flash-lite'

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
        print(f"[WARN 503] Primary model error: {e}")

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
        print(f"[ERROR 503] Fallback failed: {final_error}")
        raise final_error

async def generate_image(prompt: str) -> str:
    translated_prompt = f"{prompt}, highly detailed, cinematic lighting, 8k resolution"
    encoded_prompt = urllib.parse.quote(translated_prompt)
    seed = random.randint(1, 999999)
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&model=flux&nologo=true&seed={seed}"

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    prompt = message.text.replace("/draw", "").strip()
    if not prompt:
        await message.answer(
            "🎨 **Как пользоваться генератором картинок:**\n\n"
            "Напишите команду `/draw` и укажите, что именно нужно нарисовать.\n\n"
            "**Примеры:**\n"
            "• `/draw киберпанк город`\n"
            "• `/draw футуристический автомобиль`\n"
            "• `/draw милый котик в космосе`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    version_label = "NCO 3.1 Pro" if (await db.get_user(message.from_user.id)).get('is_premium') else "NCO 2.1"
    await message.answer(f"🎨 *NeuroVision Core ({version_label}) генерирует изображение...*", parse_mode=ParseMode.MARKDOWN)
    await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    
    try:
        image_url = await generate_image(prompt)
        await message.answer_photo(photo=image_url, caption=f"🎨 **NeuroVision Core (Flux)**\n🖼 **Запрос:** _{prompt}_", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        err_msg = format_error_code("404-IMG", e)
        print(f"[ERROR-DRAW] {e}")
        await message.answer(err_msg)

@dp.message(lambda msg: msg.photo is not None)
async def photo_handler(message: types.Message):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    is_prem = bool(user.get('is_premium'))
    max_photos = 20 if is_prem else 3
    
    if user.get('photo_count', 0) >= max_photos:
        await message.answer(
            f"⚠️ **Лимит исчерпан [NCO-429]**\n"
            f"Вы исчерпали суточный лимит загрузки изображений ({max_photos}/{max_photos}).\n"
            f"Лимит обновится через 24 часа или оформите /premium."
        )
        return

    version_label = "NCO 3.1 Pro" if is_prem else "NCO 2.1"
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        photo_file = await bot.get_file(message.photo[-1].file_id)
        downloaded_file = await bot.download_file(photo_file.file_path)
        image = Image.open(BytesIO(downloaded_file.read()))

        caption = message.caption if message.caption else "Проанализируй фото."
        
        active_chat_id = await db.get_active_chat(user_id)
        history = await db.get_chat_history(user_id, active_chat_id)
        
        reply_text = await asyncio.to_thread(ask_gemini_sync, caption, image, is_prem, history)
        
        await db.save_message(user_id, active_chat_id, 'user', caption)
        await db.save_message(user_id, active_chat_id, 'model', reply_text)
        await db.increment_usage(user_id, is_photo=True)
        
        await send_long_message(message.chat.id, f"*[Обработано через {version_label}]*\n\n{reply_text}")
    except Exception as e:
        err_msg = format_error_code("503-VISION", e)
        print(f"[ERROR-IMAGE] {e}")
        await message.answer(err_msg)

@dp.message()
async def text_handler(message: types.Message):
    text_lower = message.text.lower().strip()
    
    if text_lower.startswith("нарисуй ") or text_lower.startswith("сгенерируй "):
        prompt = message.text.split(" ", 1)[1]
        version_label = "NCO 3.1 Pro" if (await db.get_user(message.from_user.id)).get('is_premium') else "NCO 2.1"
        await message.answer(f"🎨 *NeuroVision Core ({version_label}) генерирует изображение...*", parse_mode=ParseMode.MARKDOWN)
        await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
        try:
            image_url = await generate_image(prompt)
            await message.answer_photo(photo=image_url, caption=f"🎨 **NeuroVision Core (Flux)**\n🖼 **Запрос:** _{prompt}_", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            err_msg = format_error_code("404-IMG", e)
            print(f"[ERROR-DRAW] {e}")
            await message.answer(err_msg)
        return

    user_id = message.from_user.id
    user = await db.get_user(user_id)
    is_prem = bool(user.get('is_premium'))
    max_msgs = 100 if is_prem else 40
    
    if user.get('msg_count', 0) >= max_msgs:
        await message.answer(
            f"⚠️ **Лимит исчерпан [NCO-429]**\n"
            f"Вы исчерпали суточный лимит текстовых запросов ({max_msgs}/{max_msgs}).\n"
            f"Лимит обновится через 24 часа или оформите /premium."
        )
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        active_chat_id = await db.get_active_chat(user_id)
        history = await db.get_chat_history(user_id, active_chat_id)
        
        reply_text = await asyncio.to_thread(ask_gemini_sync, message.text, None, is_prem, history)
        
        await db.save_message(user_id, active_chat_id, 'user', message.text)
        await db.save_message(user_id, active_chat_id, 'model', reply_text)
        await db.increment_usage(user_id, is_photo=False)
        
        await send_long_message(message.chat.id, reply_text)
    except Exception as e:
        err_msg = format_error_code("503-TEXT", e)
        print(f"[ERROR-TEXT] {e}")
        await message.answer(err_msg)

async def main():
    await db.init_db()
    await set_bot_commands(bot)
    print("NeuroCore Omega успешно запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())

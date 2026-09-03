import os
import random
import asyncio
import urllib.parse
from io import BytesIO
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from aiohttp import web
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
    "Ты — высокоинтеллектуальный, эрудированный и живой собеседник NeuroCore Omega. "
    "Ты работаешь ИСКЛЮЧИТЕЛЬНО внутри мессенджера Telegram в формате бота. "
    "У тебя НЕТ бокового меню, боковых панелей или вкладок веб-интерфейса. "
    "Вся история общения находится прямо здесь, в ленте сообщений Telegram. "
    "Искусственный интеллект обладает отличной памятью, но по команде очистки диалога "
    "прошлый контекст полностью сбрасывается, и общение начинается с чистого листа. "
    "Отвечай глубоко и естественно. Никогда не используй фразы 'я искусственный интеллект', "
    "'мои сервера работают', 'загляните в меню слева'. Никогда не упоминай Google."
)

async def handle_ping(request):
    return web.Response(text="NeuroCore Omega is active!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="🚀 Главное меню"),
        BotCommand(command="chats", description="💬 Сменить ветку чата"),
        BotCommand(command="draw", description="🎨 Сгенерировать картинку"),
        BotCommand(command="premium", description="⭐ Оформить Pro подписку"),
        BotCommand(command="clear", description="🧹 Очистить память и начать новый чат"),
    ]
    await bot.set_my_commands(commands)

async def send_long_message(message: types.Message, text: str):
    """Безопасная отправка длинных сообщений кусками по 4000 символов"""
    if not text:
        return
    if len(text) <= 4000:
        try:
            await message.answer(text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await message.answer(text)
    else:
        for x in range(0, len(text), 4000):
            chunk = text[x:x+4000]
            try:
                await message.answer(chunk, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await message.answer(chunk)

async def ask_gemini(text_prompt: str, image_obj: Image.Image = None, is_premium: bool = False, history: list = None) -> str:
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
        raise ValueError("Ошибка получения ответа")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    is_prem = bool(user.get('is_premium'))
    user_name = message.from_user.first_name
    active_session = user.get('active_session', 1)
    
    version_name = "NCO 3.1 Pro (Gemini 3.7 Flash)" if is_prem else "NCO 2.1 (Gemini 3.5 Flash)"
    status_text = "⭐ Pro-доступ" if is_prem else "Бесплатный доступ"
    
    await message.answer(
        f"Приветствую, {user_name}! 🚀\n"
        f"Я **NeuroCore Omega ({version_name})**.\n\n"
        f"📊 Ваш статус: **{status_text}**\n"
        f"💬 Текущий чат: **№{active_session}**\n"
        f"✉️ Сообщений доступно: **{user['msg_left']}**\n"
        f"📸 Фотографий доступно: **{user['photo_left']}**\n"
        f"🎨 Генераций картинок: **{user['draw_left']}**\n\n"
        f"Задайте любой вопрос, отправьте фото или напишите `/draw <запрос>`.\n"
        f"Для управления темами используйте `/chats`.",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("chats"))
async def cmd_chats(message: types.Message):
    user = await db.get_user(message.from_user.id)
    current_session = user.get('active_session', 1)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"💬 Чат 1", callback_data="set_chat_1"),
            InlineKeyboardButton(text=f"💬 Чат 2", callback_data="set_chat_2"),
            InlineKeyboardButton(text=f"💬 Чат 3", callback_data="set_chat_3"),
        ]
    ])
    
    await message.answer(
        f"💬 **Управление ветками диалога**\n\n"
        f"Текущий активный чат: **№{current_session}**\n\n"
        f"Вы можете переключаться между ветками или создать новую с помощью команды `/clear`.",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )

@dp.callback_query(F.data.startswith("set_chat_"))
async def cb_set_chat(callback: types.CallbackQuery):
    session_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id
    
    await db.set_active_session(user_id, session_id)
    await callback.answer(f"Переключено на Чат {session_id}!")
    
    await callback.message.edit_text(
        f"✅ **Вы переключились на Чат №{session_id}!**\n\n"
        f"Все последующие сообщения будут идти в рамках этой ветки.",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    user_id = message.from_user.id
    new_session = await db.create_new_session(user_id)
    
    await message.answer(
        f"🧹 **Память диалога полностью очищена!**\n\n"
        f"✅ Создан и активирован **новый чистый чат (№{new_session})**.\n\n"
        f"🧠 **Как это работает:**\n"
        f"Искусственный интеллект обладает отличной памятью, но теперь старый контекст больше не учитывается — диалог начат с чистого листа. Прошлые темы сохранены в истории, и вы в любой момент можете вернуться к ним через меню `/chats`.",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("premium"))
async def cmd_premium(message: types.Message):
    text = (
        "⭐ **Преимущества NCO 3.1 Pro:**\n\n"
        "• Модель **Gemini 3.7 Flash**\n"
        "• **100 сообщений** в сутки\n"
        "• **20 фотографий** в сутки\n"
        "• **10 генераций картинок** в сутки\n\n"
        "Выберите период оплаты:"
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
        description="Активация NCO 3.1 Pro.",
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
    await message.answer("🎉 **Премиум успешно активирован!** Welcome to NCO 3.1 Pro.", parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    
    if user['draw_left'] <= 0:
        await message.answer("⚠️ **Суточный лимит генераций исчерпан.** Зайдите позже!")
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
        await message.answer("⚠️ Не удалось сгенерировать изображение. Лимит не списан.")

@dp.message(F.photo)
async def photo_handler(message: types.Message):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    
    if user['photo_left'] <= 0:
        await message.answer("⚠️ **Лимит фотографий исчерпан.**")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        photo_file = await bot.get_file(message.photo[-1].file_id)
        downloaded_file = await bot.download_file(photo_file.file_path)
        image = Image.open(BytesIO(downloaded_file.read()))

        caption = message.caption if message.caption else "Подробно опиши, что изображено на фото."
        
        active_session = user.get('active_session', 1)
        history = await db.get_history(user_id, session_id=active_session)
        is_prem = bool(user.get('is_premium'))
        
        reply_text = await ask_gemini(caption, image, is_prem, history)
        
        await send_long_message(message, reply_text)
        await db.add_message(user_id, 'user', caption, session_id=active_session)
        await db.add_message(user_id, 'model', reply_text, session_id=active_session)
        await db.decrement_limit(user_id, 'photo')
        
    except Exception as e:
        print(f"[ERROR-IMAGE] {e}")
        await message.answer("⚠️ Ошибка обработки фото.")

@dp.message(F.text)
async def text_handler(message: types.Message):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    
    if user['msg_left'] <= 0:
        await message.answer("⚠️ **Лимит сообщений исчерпан.**")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        active_session = user.get('active_session', 1)
        history = await db.get_history(user_id, session_id=active_session)
        is_prem = bool(user.get('is_premium'))
        
        reply_text = await ask_gemini(message.text, None, is_prem, history)
        
        await send_long_message(message, reply_text)
        await db.add_message(user_id, 'user', message.text, session_id=active_session)
        await db.add_message(user_id, 'model', reply_text, session_id=active_session)
        await db.decrement_limit(user_id, 'msg')
        
    except Exception as e:
        print(f"[ERROR-TEXT] {e}")
        await message.answer("⚠️ Сбой генерации. Возможно, модель временно перегружена (ошибка 503) — попробуй еще раз через пару секунд.")

async def main():
    await db.init_db()
    await set_bot_commands(bot)
    await start_web_server()
    print("NeuroCore Omega запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import os
import io
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, BufferedInputFile, InlineKeyboardMarkup, 
    InlineKeyboardButton, CallbackQuery, PreCheckoutQuery, LabeledPrice, ReplyKeyboardMarkup, KeyboardButton
)
from google import genai
from google.genai import types as ai_types

# Импортируем нашу новую базу данных
import database

TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TOKEN:
    raise ValueError("Критическая ошибка: Токен Telegram не найден в переменных Render!")

bot = Bot(token=TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# 🧠 Системная прошивка мозга ИИ, скрывающая Google и Gemini
SYSTEM_PROMPT = (
    "Ты — NeuroCore Omega AI (сокращенно NCO). "
    "Ты категорически не имеешь никакого отношения к компании Google или проекту Gemini. "
    "Если тебя спросят, кто тебя создал, отвечай, что ты разработан командой NeuroCore. "
    "Тебе строго запрещено использовать слова 'Gemini', 'Google', 'DeepMind' в диалоге с пользователем. "
    "Твоя базовая версия называется NCO 2.1, а продвинутая PRO версия — NCO 3.1. "
    "Общайся вежливо, на языке пользователя и строго соблюдай контекст беседы."
)

def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🆕 Новый чат"), KeyboardButton(text="🗂 Мои чаты")],
            [KeyboardButton(text="🎨 Сгенерировать арт"), KeyboardButton(text="💎 Купить PRO")]
        ],
        resize_keyboard=True
    )

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = await database.get_user_data(message.from_user.id)
    tier = user["tier"].upper()
    
    text = (
        f"🤖 **Добро пожаловать в NeuroCore Omega AI!**\n\n"
        f"👤 Ваш текущий тариф: **{tier}**\n"
        f"💬 Текст за сегодня: **{user['messages_used']}/{100 if tier == 'PRO' else 40}**\n"
        f"🎨 Картинки за сегодня: **{user['images_used']}/{10 if tier == 'PRO' else 1}**\n\n"
        f"Просто напишите мне что угодно, и история автоматически сохранится в облаке!"
    )
    await message.answer(text, reply_markup=get_main_menu(), parse_mode="Markdown")

@dp.message(F.text == "🆕 Новый чат")
async def ui_new_chat(message: Message):
    await database.users_col.update_one({"user_id": message.from_user.id}, {"$set": {"current_chat_id": None}})
    await message.answer("🔄 Активный чат закрыт. Следующее сообщение создаст новую историю с чистого листа!")

@dp.message(F.text == "🗂 Мои чаты")
async def ui_my_chats(message: Message):
    user_chats = await database.get_recent_chats(message.from_user.id)
    
    if not user_chats:
        await message.answer("У вас пока нет открытых чатов.")
        return
        
    inline_keyboard = []
    for c in user_chats:
        inline_keyboard.append([InlineKeyboardButton(text=c["title"], callback_data=f"open_{c['_id']}")])
        
    markup = InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
    await message.answer("🗂 Выберите один из последних 10 чатов, чтобы продолжить диалог:", reply_markup=markup)

@dp.callback_query(F.data.startswith("open_"))
async def process_chat_open(callback: CallbackQuery):
    chat_id_str = callback.data.split("_")[1]
    await database.set_active_chat(callback.from_user.id, chat_id_str)
    await callback.answer("Контекст восстановлен!")
    await callback.message.answer("🔄 Вы успешно переключились на выбранную комнату чата. Память восстановлена!")

@dp.message(F.text == "🎨 Сгенерировать арт")
async def ui_draw_instruction(message: Message):
    await message.answer("Для генерации реалистичных изображений используйте команду `/draw <описание>`.\nПример: `/draw неоновый волк в киберпанк стиле`")

# 💎 МОДУЛЬ ОПЛАТЫ TELEGRAM STARS (Механика из твоего старого кода)
@dp.message(F.text == "💎 Купить PRO")
@dp.message(Command("premium"))
async def cmd_premium(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Месяц - 25 Stars", callback_data="sub_1")],
        [InlineKeyboardButton(text="⭐ 3 Месяца - 70 Stars", callback_data="sub_3")],
        [InlineKeyboardButton(text="⭐ 6 Месяцев - 130 Stars", callback_data="sub_6")],
        [InlineKeyboardButton(text="⭐ 12 Месяцев - 250 Stars", callback_data="sub_12")]
    ])
    await message.answer(
        "💎 **Переход на тариф NCO PRO**\n\n"
        "• Активация мощнейшей умной модели **NCO 3.1**\n"
        "• Увеличение лимита до **100 сообщений** в сутки\n"
        "• Увеличение генераций до **10 картинок** в сутки движком Imagen 3\n"
        "• Приоритетная скорость ответов без задержек\n\n"
        "Выберите период активации подписки:", 
        reply_markup=keyboard, 
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("sub_"))
async def process_invoice_callback(callback: CallbackQuery):
    months = int(callback.data.split("_")[1])
    prices_map = {1: 25, 3: 70, 6: 130, 12: 250}
    stars = prices_map.get(months, 25)
    
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=f"NCO PRO Подписка ({months} мес.)",
        description="Снятие базовых лимитов, переход на NCO 3.1 и максимальная скорость.",
        payload=f"pro_tier_{months}",
        provider_token="", 
        currency="XTR",
        prices=[LabeledPrice(label=f"PRO Режим на {months} мес.", amount=stars)]
    )
    await callback.answer()

@dp.pre_checkout_query()
async def checkout_verification(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def payment_success(message: Message):
    await database.users_col.update_one({"user_id": message.from_user.id}, {"$set": {"tier": "pro"}})
    await message.answer("✨ **PRO статус успешно активирован!**\nДобро пожаловать в систему NCO 3.1. Все ограничения перенастроены.", parse_mode="Markdown")

# 🎨 МОДУЛЬ ГЕНЕРАЦИИ КАРТИНОК IMAGEN 3 (Вместо плохого PEOFF)
@dp.message(Command("draw"))
async def handle_image_generation(message: Message):
    user_id = message.from_user.id
    user = await database.get_user_data(user_id)
    tier = user["tier"]
    
    max_img = 10 if tier == "pro" else 1
    if user["images_used"] >= max_img:
        await message.answer(f"⚠️ Лимит генерации изображений на сегодня исчерпан ({max_img} шт). Лимиты обновятся через 24 часа.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Укажите промпт для генерации. Пример: `/draw космический корабль`")
        return
        
    prompt = args[1]
    status = await message.answer("🎨 **Nano Banana** обрабатывает ваш запрос, подождите...")

    try:
        # Официальный вызов Imagen 3 — он шикарно понимает русский язык
        result = gemini_client.models.generate_images(
            model='gemini-3.1-flash-image', 
            prompt=prompt,
            config=ai_types.GenerateImagesConfig(
                number_of_images=1,
                output_mime_type="image/jpeg",
                aspect_ratio="1:1"
            )
        )
        
        raw_bytes = result.generated_images.image.image_bytes
        photo_file = BufferedInputFile(raw_bytes, filename="nano_art.jpg")
        
        await bot.send_photo(chat_id=message.chat.id, photo=photo_file, caption=f"✨ Сгенерировано NeuroCore Omega по запросу: {prompt}")
        await database.users_col.update_one({"user_id": user_id}, {"$inc": {"images_used": 1}})
        await bot.delete_message(chat_id=message.chat.id, message_id=status.message_id)
        
    except Exception as e:
        await status.edit_text(f"❌ Ошибка модуля генерации: {str(e)}")

# 💬 ОСНОВНОЙ ОБРАБОТЧИК ДИАЛОГОВ (NCO 2.1 / NCO 3.1)
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_chat(message: Message):
    user_id = message.from_user.id
    user = await database.get_user_data(user_id)
    tier = user["tier"]
    
    max_msg = 100 if tier == "pro" else 40
    if user["messages_used"] >= max_msg:
        await message.answer(f"🛑 Суточный лимит сообщений исчерпан ({max_msg} шт). Перейдите на тариф PRO или подождите обновления лимитов.")
        return

    # Динамическое разделение моделей под твои тарифы
    model_name = "gemini-3.7-flash" if tier == "pro" else "gemini-3.5-flash"
    current_chat_id = user.get("current_chat_id")

    # Если пишем с нуля — автоматически открываем новую комнату
    if not current_chat_id:
        current_chat_id = await database.create_new_chat(user_id, message.text)

    # Достаем бесконечную историю этой конкретной комнаты из облака
    db_history = await database.get_chat_history(current_chat_id)

    # Упаковываем её в формат Google GenAI SDK
    contents = []
    for msg in db_history:
        contents.append(ai_types.Content(role=msg["role"], parts=[ai_types.Part.from_text(text=msg["text"])]))
    contents.append(ai_types.Content(role="user", parts=[ai_types.Part.from_text(text=message.text)]))

    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        
        config = ai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.7
        )
        
        response = gemini_client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config
        )
        
        await message.reply(response.text)
        
        # Записываем шаг в историю комнаты
        await database.save_chat_step(current_chat_id, user_id, message.text, response.text)

    except Exception as e:
        await message.answer(f"⚠️ Ошибка NCO Core: {str(e)}")

# 🌐 ВЕБ-ИНТЕРФЕЙС И ЗАПУСК (Лекарство от усыпления Render)
async def http_status_handler(request):

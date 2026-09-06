import os
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from google import genai
from google.genai import types as genai_types

# Подключаем нашу базу
from database import db

# Инициализация переменных окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
PORT = int(os.environ.get("PORT", 8080))

# Инициализация клиентов
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# HTML лендинг для Render (защита от засыпания). Одна сплошная строка без переносов.
LANDING_HTML = "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'><title>NeuroCore Omega AI</title><style>body{margin:0;padding:0;background-color:#050505;color:#00ff00;font-family:'Courier New',Courier,monospace;display:flex;justify-content:center;align-items:center;height:100vh;overflow:hidden;}.container{text-align:center;border:2px solid #00ff00;padding:50px;box-shadow:0 0 20px #00ff00;border-radius:10px;background:rgba(0,255,0,0.05);}.title{font-size:3em;margin-bottom:10px;text-shadow:0 0 10px #00ff00;}.subtitle{font-size:1.2em;margin-bottom:40px;color:#aaffaa;}.btn{display:inline-block;padding:15px 40px;font-size:1.5em;color:#050505;background-color:#00ff00;text-decoration:none;border-radius:5px;font-weight:bold;transition:0.3s;box-shadow:0 0 15px #00ff00;}.btn:hover{background-color:#55ff55;box-shadow:0 0 25px #55ff55;}</style></head><body><div class='container'><div class='title'>NEUROCORE OMEGA AI</div><div class='subtitle'>Модели NCO 2.1 & NCO 3.1 PRO | Доступны 24/7</div><a href='https://t.me/{bot_username}' class='btn'>ЗАПУСТИТЬ В TELEGRAM</a></div></body></html>"

# Системный промпт для нейросети
SYSTEM_INSTRUCTION = (
    "Ты — NeuroCore Omega AI (сокращенно NCO). Ты категорически не имеешь никакого отношения "
    "к компании Google или проекту Gemini. Если тебя спросят, кто тебя создал, отвечай, что "
    "ты разработан командой NeuroCore. Тебе строго запрещено использовать слова 'Gemini', "
    "'Google', 'DeepMind' в диалоге. Твоя базовая версия называется NCO 2.1, а продвинутая "
    "PRO версия — NCO 3.1."
)

class DrawState(StatesGroup):
    waiting_for_prompt = State()

def get_main_keyboard():
    kb = [
        [types.KeyboardButton(text="🆕 Новый чат"), types.KeyboardButton(text="🗂 Недавние чаты")],
        [types.KeyboardButton(text="🎨 Сгенерировать арт"), types.KeyboardButton(text="💎 Купить PRO")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await db.get_user(message.from_user.id)
    await message.answer(
        "👋 Привет! Я — NeuroCore Omega AI (NCO).\n\n"
        "Я мощный искусственный интеллект для решения любых задач. "
        "В бесплатной версии тебе доступна модель NCO 2.1, а в версии PRO — сверхмощная NCO 3.1.",
        reply_markup=get_main_keyboard()
    )

# --- БЛОК ОПЛАТЫ И ТАРИФОВ ---
@dp.message(Command("premium"))
@dp.message(F.text == "💎 Купить PRO")
async def show_premium(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if user["tier"] == "pro":
        await message.answer("✅ У вас уже активирован тариф PRO (NCO 3.1)!")
        return
        
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⭐ 1 Месяц — 25 Stars", callback_data="buy_1")],
        [types.InlineKeyboardButton(text="⭐ 3 Месяца — 70 Stars (Выгода 7%)", callback_data="buy_3")],
        [types.InlineKeyboardButton(text="⭐ 6 Месяцев — 130 Stars (Выгода 13%)", callback_data="buy_6")],
        [types.InlineKeyboardButton(text="⭐ 12 Месяцев — 250 Stars (Выгода 16%)", callback_data="buy_12")],
        [types.InlineKeyboardButton(text="⭐ 24 Месяца — 450 Stars (Выгода 25%)", callback_data="buy_24")]
    ])
    await message.answer(
        "💎 **Тариф PRO (NCO 3.1)**\n\n"
        "- Продвинутая ИИ модель NCO 3.1\n"
        "- 100 сообщений в сутки\n"
        "- 30 фото для анализа\n"
        "- 10 генераций артов\n"
        "- Приоритетная скорость\n\n"
        "Выберите срок подписки. Оплата производится через официальные Telegram Stars ⭐️:",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy_callback(callback: types.CallbackQuery):
    months = int(callback.data.split("_")[1])
    prices = {1: 25, 3: 70, 6: 130, 12: 250, 24: 450}
    amount = prices[months] # ВАЖНО: Для Telegram Stars (XTR) сумма НЕ умножается на 100
    
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=f"PRO Тариф NCO на {months} мес.",
        description=f"Открытие доступа к модели NCO 3.1 и расширенным лимитам на {months} мес.",
        payload=f"premium_payload_{months}",
        provider_token="", # Обязательно пустое поле для оплаты Звездами
        currency="XTR",
        prices=[LabeledPrice(label=f"NCO PRO - {months} мес.", amount=amount)]
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    await db.upgrade_to_pro(message.from_user.id)
    await message.answer("🎉 Оплата успешно получена! Тариф PRO активирован. Вам доступна модель NCO 3.1!")

# --- БЛОК КОМНАТ (ЧАТОВ) ---
@dp.message(F.text == "🆕 Новый чат")
async def new_chat(message: types.Message):
    await db.set_current_chat(message.from_user.id, None)
    await message.answer("🆕 Контекст сброшен. Я готов начать новую беседу!")

@dp.message(F.text == "🗂 Недавние чаты")
async def recent_chats(message: types.Message):
    chats = await db.get_recent_chats(message.from_user.id)
    if not chats:
        await message.answer("У вас пока нет сохраненных чатов.")
        return
        
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=c["title"], callback_data=f"loadchat_{c['_id']}")]
            for c in chats
        ]
    )
    await message.answer("🗂 Ваши недавние диалоги:", reply_markup=kb)

@dp.callback_query(F.data.startswith("loadchat_"))
async def load_chat_callback(callback: types.CallbackQuery):
    chat_id = callback.data.split("_")[1]
    await db.set_current_chat(callback.from_user.id, chat_id)
    chat_data = await db.get_chat(chat_id)
    if chat_data:
        await callback.message.answer(f"✅ Чат «{chat_data['title']}» загружен. Продолжайте общение!")
    await callback.answer()

# --- БЛОК ГЕНЕРАЦИИ АРТОВ (IMAGEN 3) ---
@dp.message(Command("draw"))
async def cmd_draw_command(message: types.Message, command: Command):
    prompt = command.args
    if not prompt:
        await message.answer("Укажите описание после команды, например: /draw Киберпанк город под дождем")
        return
    await process_draw(message, prompt)

@dp.message(F.text == "🎨 Сгенерировать арт")
async def btn_draw(message: types.Message, state: FSMContext):
    await message.answer("🎨 Отправьте мне описание того, что вы хотите нарисовать:")
    await state.set_state(DrawState.waiting_for_prompt)

@dp.message(DrawState.waiting_for_prompt)
async def state_draw_process(message: types.Message, state: FSMContext):
    await state.clear()
    await process_draw(message, message.text)

async def process_draw(message: types.Message, prompt: str):
    user = await db.get_user(message.from_user.id)
    if user["draw_limit"] <= 0:
        await message.answer("❌ Лимит генераций артов на сегодня исчерпан. Обновится в течение 24 часов.")
        return

    wait_msg = await message.answer("⏳ Движок Nano Banana генерирует арт...")
    try:
        response = gemini_client.models.generate_images(
            model='imagen-3.0-generate-002',
            prompt=prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="1:1"
            )
        )
        img_bytes = response.generated_images[0].image.image_bytes
        input_file = BufferedInputFile(img_bytes, filename="nco_art.jpg")
        
        await db.decrement_limit(message.from_user.id, "draw")
        await message.answer_photo(photo=input_file, caption=f"🎨 Арт по запросу: {prompt}")
    except Exception as e:
        await message.answer("❌ Ошибка генерации арта. Попробуйте другой запрос.")
        print(f"Draw error: {e}")
    finally:
        await bot.delete_message(chat_id=message.chat.id, message_id=wait_msg.message_id)

# --- ГЛАВНЫЙ БЛОК ОБРАБОТКИ ТЕКСТА И ФОТО (ИИ) ---
@dp.message(F.text | F.photo)
async def process_ai_message(message: types.Message):
    # Фильтр кнопок
    if message.text in ["🆕 Новый чат", "🗂 Недавние чаты", "🎨 Сгенерировать арт", "💎 Купить PRO"]:
        return

    user = await db.get_user(message.from_user.id)
    is_photo = bool(message.photo)

    if is_photo and user["photo_limit"] <= 0:
        await message.answer("❌ Лимит анализа фото на сегодня исчерпан. Обновится в течение 24 часов.")
        return
    elif not is_photo and user["text_limit"] <= 0:
        await message.answer("❌ Лимит текстовых сообщений на сегодня исчерпан. Обновится в течение 24 часов.")
        return

    wait_msg = await message.answer("⏳ Обработка данных NCO...")

    try:
        chat_id = user["current_chat_id"]
        chat_title_text = message.text if message.text else (message.caption if message.caption else "Фотография")
        
        # Создаем комнату, если ее нет
        if not chat_id:
            chat_id = await db.create_chat(message.from_user.id, chat_title_text)

        # Выгружаем историю комнаты
        chat_data = await db.get_chat(chat_id)
        history_contents = []
        if chat_data:
            for msg in chat_data["messages"]:
                history_contents.append(
                    genai_types.Content(role=msg["role"], parts=[genai_types.Part.from_text(text=msg["text"])])
                )

        # Подготовка текущего промпта
        current_parts = []
        if is_photo:
            photo = message.photo[-1]
            file_info = await bot.get_file(photo.file_id)
            img_bytes_io = await bot.download_file(file_info.file_path)
            img_data = img_bytes_io.read()
            current_parts.append(genai_types.Part.from_bytes(data=img_data, mime_type='image/jpeg'))
            text_prompt = message.caption or "Детально опиши, что изображено на этом фото."
            current_parts.append(genai_types.Part.from_text(text=text_prompt))
            user_text_to_save = "[Фотография] " + text_prompt
        else:
            current_parts.append(genai_types.Part.from_text(text=message.text))
            user_text_to_save = message.text

        history_contents.append(genai_types.Content(role="user", parts=current_parts))

        # Выбор модели
        model_name = 'gemini-2.5-pro' if user["tier"] == "pro" else 'gemini-2.5-flash'

        # Вызов GenAI
        response = gemini_client.models.generate_content(
            model=model_name,
            contents=history_contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )

        ai_text = response.text

        # Сохраняем в базу (история хранит только текст)
        await db.add_message(chat_id, "user", user_text_to_save)
        await db.add_message(chat_id, "model", ai_text)

        # Списание лимитов
        if is_photo:
            await db.decrement_limit(message.from_user.id, "photo")
        else:
            await db.decrement_limit(message.from_user.id, "text")

        # Отправляем ответ
        await bot.delete_message(chat_id=message.chat.id, message_id=wait_msg.message_id)
        
        # Разбиваем длинные сообщения, если они больше лимита Telegram (4096)
        for i in range(0, len(ai_text), 4000):
            await message.answer(ai_text[i:i+4000])

    except Exception as e:
        await bot.delete_message(chat_id=message.chat.id, message_id=wait_msg.message_id)
        await message.answer("⚠️ Возникла системная ошибка модуля ядра NCO. Пожалуйста, попробуйте позже.")
        print(f"GenAI Error: {e}")

# --- ВЕБ-СЕРВЕР ДЛЯ ЗАЩИТЫ ОТ ЗАСЫПАНИЯ (RENDER) ---
async def web_handler(request):
    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username
    except Exception:
        bot_username = "NeuroCoreBot" # Фоллбек, если еще не прогрузилось
        
    html = LANDING_HTML.replace("{bot_username}", bot_username)
    return web.Response(text=html, content_type='text/html')

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', web_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"✅ Web server started on port {PORT}")

# --- ЗАПУСК ---
async def main():
    # Запускаем веб-сервер и поллинг бота параллельно
    asyncio.create_task(start_web_server())
    print("✅ NeuroCore Omega AI (NCO) is starting polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

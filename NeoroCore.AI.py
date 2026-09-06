import os
import io
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, BufferedInputFile
from google import genai
from google.genai import types as genai_types

import database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
genai_client = genai.Client(api_key=GEMINI_API_KEY)

# Веб-сервер для поддержки активности Render 24/7
async def handle_ping(request):
    html_content = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>NeuroCore Omega AI — System Status</title>
        <style>
            body { background-color: #0d0f12; color: #00ffcc; font-family: monospace; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .container { text-align: center; border: 1px solid #00ffcc; padding: 40px; box-shadow: 0 0 20px #00ffcc; }
            h1 { font-size: 2.5em; margin-bottom: 10px; }
            p { color: #888; }
            .status { color: #00ff00; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>NEUROCORE OMEGA AI</h1>
            <p>SYSTEM STATUS: <span class="status">ONLINE (24/7 ACTIVE)</span></p>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

# --- КОМАНДА /start И ДИАЛОГИ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    await database.get_or_create_user(user_id, username)
    
    welcome_text = (
        "⚡ <b>Добро пожаловать в NeuroCore Omega AI (NCO)!</b>\n\n"
        "Я — ваш персональный ИИ-ассистент. Я поддерживаю текстовый диалог с памятью, "
        "анализ фотографий и генерацию изображений **NeuroCore Vision 4.2**.\n\n"
        "Воспользуйтесь меню ниже для управления диалогами и подпиской."
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Мои диалоги", callback_data="list_chats"),
         InlineKeyboardButton(text="➕ Новый диалог", callback_data="new_chat")],
        [InlineKeyboardButton(text="⭐ Оформить PRO", callback_data="buy_pro")]
    ])
    
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "new_chat")
async def process_new_chat(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await database.set_current_chat(user_id, None)
    await callback.message.answer("📝 **Создан новый контекст.** Напишите любое сообщение, чтобы начать диалог.")
    await callback.answer()

@dp.callback_query(F.data == "list_chats")
async def process_list_chats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    chats = await database.get_user_recent_chats(user_id)
    
    if not chats:
        await callback.message.answer("📭 У вас пока нет сохраненных диалогов.")
        await callback.answer()
        return

    buttons = []
    for chat in chats:
        buttons.append([InlineKeyboardButton(
            text=f"💬 {chat['title']}", 
            callback_data=f"select_chat_{chat['id']}"
        )])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer("📋 **Ваши последние диалоги:**", reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("select_chat_"))
async def process_select_chat(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    chat_id = callback.data.split("_")[2]
    await database.set_current_chat(user_id, chat_id)
    await callback.message.answer("✅ Диалог успешно выбран! Теперь бот помнит историю из этой комнаты.")
    await callback.answer()

# --- ПОДПИСКА И ОПЛАТА TELEGRAM STARS ---

@dp.callback_query(F.data == "buy_pro")
async def process_buy_pro(callback: types.CallbackQuery):
    text = (
        "👑 <b>Преимущества PRO-подписки (NCO 3.1 & Vision 4.2):</b>\n"
        "• Модель нового поколения NCO 3.1\n"
        "• 100 текстовых сообщений в день\n"
        "• 30 распознаваний изображений в день\n"
        "• 10 генераций артов в день (NeuroCore Vision 4.2)\n"
        "• Приоритетная скорость и увеличенная память\n\n"
        "Выберите удобный период подписки:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Месяц — 25 Stars", callback_data="buy_pro_1")],
        [InlineKeyboardButton(text="⭐ 3 Месяца — 70 Stars (Выгода 7%)", callback_data="buy_pro_3")],
        [InlineKeyboardButton(text="⭐ 6 Месяцев — 130 Stars (Выгода 13%)", callback_data="buy_pro_6")],
        [InlineKeyboardButton(text="⭐ 12 Месяцев — 250 Stars (Выгода 16%)", callback_data="buy_pro_12")],
        [InlineKeyboardButton(text="⭐ 24 Месяца — 450 Stars (Выгода 25%)", callback_data="buy_pro_24")]
    ])
    await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_pro_"))
async def process_stars_invoice(callback: types.CallbackQuery):
    months = int(callback.data.split("_")[2])
    price_map = {1: 25, 3: 70, 6: 130, 12: 250, 24: 450}
    stars_count = price_map.get(months, 25)

    prices = [LabeledPrice(label=f"PRO Подписка ({months} мес.)", amount=stars_count)]
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"PRO Подписка на {months} мес.",
        description=f"Доступ к NCO 3.1 и NeuroCore Vision 4.2 на {months} мес.",
        payload=f"pro_sub_{months}",
        provider_token="",
        currency="XTR",
        prices=prices
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("pro_sub_"):
        months = int(payload.split("_")[2])
        await database.activate_pro_subscription(message.from_user.id, months)
        await message.answer(f"🎉 <b>Оплата прошла успешно!</b>\nPRO-подписка активирована на {months} мес.", parse_mode="HTML")

# --- ГЕНЕРАЦИЯ КАРТИНОК NANO BANANA (NeuroCore Vision 4.2) ---

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    user_id = message.from_user.id
    prompt = message.text.replace("/draw", "").strip()

    if not prompt:
        await message.answer("⚠️ Укажите описание изображения после команды. Пример:\n`/draw Киберпанк город в неоновых огнях`", parse_mode="Markdown")
        return

    allowed = await database.check_and_increment_limit(user_id, "draw")
    if not allowed:
        await message.answer("❌ Достигнут суточный лимит генераций. Оформите PRO-подписку!")
        return

    msg = await message.answer("🎨 **NeuroCore Vision 4.2** генерирует арт, подождите...")

    try:
        # Модель Nano Banana для генерации изображений
        result = genai_client.models.generate_images(
            model='nano-banana',
            prompt=prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="1:1"
            )
        )
        for generated_image in result.generated_images:
            image_bytes = generated_image.image.image_bytes
            photo = BufferedInputFile(image_bytes, filename="vision.png")
            await message.answer_photo(
                photo=photo, 
                caption=f"🎨 **NeuroCore Vision 4.2**\n📝 **Запрос:** {prompt}", 
                parse_mode="Markdown"
            )
        await msg.delete()
    except Exception as e:
        logger.error(f"Error drawing image with Nano Banana: {e}")
        await msg.edit_text("⚠️ Ошибка при генерации изображения. Попробуйте другой запрос.")

# --- АНАЛИЗ ИЗОБРАЖЕНИЙ ---

@dp.message(F.photo)
async def handle_photo_message(message: types.Message):
    user_id = message.from_user.id
    allowed = await database.check_and_increment_limit(user_id, "photo")
    if not allowed:
        await message.answer("❌ Достигнут суточный лимит анализа фотографий.")
        return

    user = await database.get_or_create_user(user_id)
    tier = user.get("tier", "free")
    model_name = database.TIERS[tier]["genai_model"]

    photo = message.photo[-1]
    photo_file = await bot.get_file(photo.file_id)
    photo_bytes = await bot.download_file(photo_file.file_path)

    caption = message.caption or "Что изображено на этом фото?"

    try:
        response = genai_client.models.generate_content(
            model=model_name,
            contents=[
                genai_types.Part.from_bytes(data=photo_bytes.read(), mime_type="image/jpeg"),
                caption
            ]
        )
        await message.answer(response.text)
    except Exception as e:
        logger.error(f"Error photo processing: {e}")
        await message.answer("⚠️ Ошибка при обработке изображения.")

# --- ТЕКСТОВЫЙ ДИАЛОГ С ПАМЯТЬЮ ---

@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    allowed = await database.check_and_increment_limit(user_id, "text")
    if not allowed:
        await message.answer("❌ Достигнут суточный лимит сообщений. Перейдите на PRO!")
        return

    user = await database.get_or_create_user(user_id)
    tier = user.get("tier", "free")
    model_name = database.TIERS[tier]["genai_model"]
    chat_id = user.get("current_chat_id")

    # Создание комнаты, если диалог еще не начат
    if not chat_id:
        chat_id = await database.create_new_chat(user_id, user_text)

    history = await database.get_chat_history(chat_id)
    
    # Формирование полного контекста диалога
    contents = []
    for msg in history:
        contents.append(f"{'User' if msg['role'] == 'user' else 'Assistant'}: {msg['content']}")
    contents.append(f"User: {user_text}")

    try:
        response = genai_client.models.generate_content(
            model=model_name,
            contents="\n".join(contents)
        )
        reply_text = response.text
        
        # Сохранение диалога в базу данных
        await database.add_message_to_chat(chat_id, "user", user_text)
        await database.add_message_to_chat(chat_id, "model", reply_text)
        
        await message.answer(reply_text)
    except Exception as e:
        logger.error(f"Error text generation: {e}")
        await message.answer("⚠️ Ошибка при обработке запроса. Попробуйте еще раз.")

# --- ЗАПУСК ВЕБ-СЕРВЕРА И БОТА ---

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    logger.info(f"Web server running on port {port}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    

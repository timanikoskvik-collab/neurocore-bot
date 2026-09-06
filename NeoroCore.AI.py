import os
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton
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

def get_main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🆕 Новый чат"), KeyboardButton(text="📁 Недавние чаты")],
            [KeyboardButton(text="🎨 Сгенерировать арт"), KeyboardButton(text="⭐ PRO Подписка")]
        ],
        resize_keyboard=True
    )

async def handle_ping(request):
    return web.Response(text="<h1>NEUROCORE OMEGA AI - ONLINE</h1>", content_type='text/html')

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    await database.get_or_create_user(user_id, username)
    
    welcome_text = (
        "⚡ <b>Добро пожаловать в NeuroCore Omega AI!</b>\n\n"
        "Задавайте любые вопросы, отправляйте фото или используйте меню ниже."
    )
    
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_reply_keyboard())

# --- УПРАВЛЕНИЕ ЧАТАМИ (КОМАНДЫ И КНОПКИ) ---

@dp.message(Command("new"))
@dp.message(F.text == "🆕 Новый чат")
async def process_new_chat(message: types.Message):
    user_id = message.from_user.id
    await database.set_current_chat(user_id, None)
    await message.answer("✨ <b>С чистого листа!</b> Отправьте любое сообщение, чтобы начать новый контекст.", parse_mode="HTML")

@dp.message(Command("chats"))
@dp.message(F.text == "📁 Недавние чаты")
async def process_list_chats(message: types.Message):
    user_id = message.from_user.id
    chats = await database.get_user_recent_chats(user_id)
    
    if not chats:
        await message.answer("📭 У вас пока нет сохраненных диалогов в базе данных.")
        return

    buttons = []
    for chat in chats:
        buttons.append([InlineKeyboardButton(
            text=f"💬 {chat['title']}", 
            callback_data=f"select_chat_{chat['id']}"
        )])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("📂 <b>Ваши реальные сохраненные диалоги:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("select_chat_"))
async def process_select_chat(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    chat_id = callback.data.split("_")[2]
    await database.set_current_chat(user_id, chat_id)
    await callback.message.answer("✅ Диалог переключен! Теперь история сообщений берется из этой комнаты.")
    await callback.answer()

# --- ПОДПИСКА И ИНФО ---

@dp.message(F.text == "🎨 Сгенерировать арт")
async def btn_draw_info(message: types.Message):
    await message.answer("🎨 Чтобы сгенерировать арт, отправьте команду `/draw` и описание.\n\n*Пример:* `/draw Неоновый город будущего`", parse_mode="Markdown")

@dp.message(F.text == "⭐ PRO Подписка")
@dp.callback_query(F.data == "buy_pro")
async def process_buy_pro(event: types.Message | types.CallbackQuery):
    text = (
        "👑 <b>Преимущества PRO-подписки (NCO 3.1 & Vision 4.2):</b>\n"
        "• Модель NCO 3.1 (быстрый ИИ)\n"
        "• 100 текстовых сообщений в день\n"
        "• 30 распознаваний фото в день\n"
        "• 10 генераций картинок в день\n\n"
        "Выберите период оплаты:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Месяц — 25 Stars", callback_data="buy_pro_1")],
        [InlineKeyboardButton(text="⭐ 3 Месяца — 70 Stars", callback_data="buy_pro_3")],
        [InlineKeyboardButton(text="⭐ 6 Месяцев — 130 Stars", callback_data="buy_pro_6")],
        [InlineKeyboardButton(text="⭐ 12 Месяцев — 250 Stars", callback_data="buy_pro_12")]
    ])
    
    if isinstance(event, types.Message):
        await event.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await event.message.answer(text, parse_mode="HTML", reply_markup=kb)
        await event.answer()

@dp.callback_query(F.data.startswith("buy_pro_"))
async def process_stars_invoice(callback: types.CallbackQuery):
    months = int(callback.data.split("_")[2])
    price_map = {1: 25, 3: 70, 6: 130, 12: 250}
    stars_count = price_map.get(months, 25)

    prices = [LabeledPrice(label=f"PRO Подписка ({months} мес.)", amount=stars_count)]
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"PRO Подписка на {months} мес.",
        description=f"Активация доступа NCO 3.1 на {months} мес.",
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
        await message.answer(f"🎉 <b>PRO-подписка активирована на {months} мес.!</b>", parse_mode="HTML")

# --- ГЕНЕРАЦИЯ КАРТИНОК ---

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    user_id = message.from_user.id
    prompt = message.text.replace("/draw", "").strip()

    if not prompt:
        await message.answer("⚠️ Укажите описание картинки после `/draw`. Пример:\n`/draw Кот в шлеме`", parse_mode="Markdown")
        return

    allowed = await database.check_and_increment_limit(user_id, "draw")
    if not allowed:
        await message.answer("❌ Достигнут суточный лимит генераций. Оформите PRO-подписку!")
        return

    msg = await message.answer("🎨 **NeuroCore Vision 4.2** генерирует арт...")

    try:
        result = genai_client.models.generate_images(
            model='imagen-3.0-fast-generate-001',
            prompt=prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="1:1"
            )
        )
        for generated_image in result.generated_images:
            image_bytes = generated_image.image.image_bytes
            photo = BufferedInputFile(image_bytes, filename="art.png")
            await message.answer_photo(
                photo=photo, 
                caption=f"🎨 **NeuroCore Vision 4.2**\n📝 {prompt}", 
                parse_mode="Markdown"
            )
        await msg.delete()
    except Exception as e:
        logger.error(f"Error drawing image: {e}")
        await msg.edit_text("⚠️ Ошибка при генерации изображения. Попробуйте еще раз.")

# --- ТЕКСТОВЫЕ СООБЩЕНИЯ ---

@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    allowed = await database.check_and_increment_limit(user_id, "text")
    if not allowed:
        await message.answer("❌ Лимит сообщений на сегодня исчерпан. Обновление через 24 часа или оформите PRO!")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    user = await database.get_or_create_user(user_id)
    tier = user.get("tier", "free")
    model_name = database.TIERS[tier]["genai_model"]
    chat_id = user.get("current_chat_id")

    if not chat_id:
        chat_id = await database.create_new_chat(user_id, user_text)

    history = await database.get_chat_history(chat_id, limit=10)
    
    contents = []
    for msg in history:
        role = "user" if msg['role'] == "user" else "model"
        contents.append(genai_types.Content(
            role=role,
            parts=[genai_types.Part.from_text(text=msg['content'])]
        ))
    
    contents.append(genai_types.Content(
        role="user",
        parts=[genai_types.Part.from_text(text=user_text)]
    ))

    try:
        response = genai_client.models.generate_content(
            model=model_name,
            contents=contents
        )
        reply_text = response.text
        
        await database.add_message_to_chat(chat_id, "user", user_text)
        await database.add_message_to_chat(chat_id, "model", reply_text)
        
        await message.answer(reply_text)
    except Exception as e:
        logger.error(f"Error text generation: {e}")
        await message.answer("⚠️ Ошибка генерации ответа. Попробуйте еще раз.")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
            

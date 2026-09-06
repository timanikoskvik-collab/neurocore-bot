import os
import asyncio
import logging
import urllib.parse
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton
from google import genai

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
        "Задавайте любые вопросы, управляйте историей диалогов и создавайте арты."
    )
    
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_reply_keyboard())

@dp.message(Command("new"))
@dp.message(F.text == "🆕 Новый чат")
async def process_new_chat(message: types.Message):
    user_id = message.from_user.id
    await database.set_current_chat(user_id, None)
    await message.answer("✨ <b>Контекст сброшен!</b> Напишите сообщение, чтобы начать новый диалог.", parse_mode="HTML")

@dp.message(Command("chats"))
@dp.message(F.text == "📁 Недавние чаты")
async def process_list_chats(message: types.Message):
    user_id = message.from_user.id
    chats = await database.get_user_recent_chats(user_id)
    
    if not chats:
        await message.answer("📭 У вас пока нет сохранённых диалогов.")
        return

    buttons = []
    for chat in chats:
        buttons.append([InlineKeyboardButton(
            text=f"💬 {chat['title']}", 
            callback_data=f"select_chat_{chat['id']}"
        )])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("📂 <b>Ваши сохранённые диалоги:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("select_chat_"))
async def process_select_chat(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    chat_id = callback.data.split("_")[2]
    await database.set_current_chat(user_id, chat_id)
    await callback.message.answer("✅ Диалог успешно выбран!")
    await callback.answer()

@dp.message(F.text == "🎨 Сгенерировать арт")
async def btn_draw_info(message: types.Message):
    await message.answer("🎨 Чтобы сгенерировать арт, отправьте команду `/draw` и описание.\n\n*Пример:* `/draw Киберпанк город`", parse_mode="Markdown")

@dp.message(F.text == "⭐ PRO Подписка")
@dp.callback_query(F.data == "buy_pro")
async def process_buy_pro(event: types.Message | types.CallbackQuery):
    text = (
        "👑 <b>Преимущества NCO 3.1 PRO:</b>\n"
        "• Модель NCO 3.1 PRO (Gemini 3.7 Flash)\n"
        "• 200 текстовых сообщений в день\n"
        "• 20 генераций картинок в день\n\n"
        "Выберите период подписки:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Месяц — 25 Stars", callback_data="buy_pro_1")],
        [InlineKeyboardButton(text="⭐ 3 Месяца — 70 Stars", callback_data="buy_pro_3")],
        [InlineKeyboardButton(text="⭐ 6 Месяцев — 130 Stars", callback_data="buy_pro_6")],
        [InlineKeyboardButton(text="⭐ 12 Месяцев — 240 Stars", callback_data="buy_pro_12")],
        [InlineKeyboardButton(text="⭐ 24 Месяца — 450 Stars", callback_data="buy_pro_24")]
    ])
    
    if isinstance(event, types.Message):
        await event.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await event.message.answer(text, parse_mode="HTML", reply_markup=kb)
        await event.answer()

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    user_id = message.from_user.id
    prompt = message.text.replace("/draw", "").strip()

    if not prompt:
        await message.answer("⚠️ Укажите описание после `/draw`. Пример:\n`/draw Космический корабль`", parse_mode="Markdown")
        return

    allowed = await database.check_and_increment_limit(user_id, "draw")
    if not allowed:
        await message.answer("❌ Достигнут суточный лимит генераций.")
        return

    msg = await message.answer("🎨 **NeuroCore Vision** генерирует арт...")

    try:
        encoded_prompt = urllib.parse.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                    photo = BufferedInputFile(image_data, filename="art.png")
                    await message.answer_photo(
                        photo=photo,
                        caption=f"🎨 **NeuroCore Vision**\n📝 {prompt}",
                        parse_mode="Markdown"
                    )
                    await msg.delete()
                else:
                    await msg.edit_text("⚠️ Ошибка сервера генерации. Попробуйте еще раз.")
    except Exception as e:
        logger.error(f"Error drawing image: {e}")
        await msg.edit_text("⚠️ Не удалось сгенерировать изображение.")

@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    allowed = await database.check_and_increment_limit(user_id, "text")
    if not allowed:
        await message.answer("❌ Лимит сообщений на сегодня исчерпан.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    user = await database.get_or_create_user(user_id)
    tier = user.get("tier", "free")
    model_name = database.TIERS[tier]["genai_model"]
    chat_id = user.get("current_chat_id")

    if not chat_id:
        chat_id = await database.create_new_chat(user_id, user_text)

    history = await database.get_chat_history(chat_id, limit=10)
    
    prompt_context = ""
    for msg in history:
        role_label = "Пользователь" if msg['role'] == "user" else "Ассистент"
        prompt_context += f"{role_label}: {msg['content']}\n"
    
    prompt_context += f"Пользователь: {user_text}\nАссистент:"

    try:
        response = genai_client.models.generate_content(
            model=model_name,
            contents=prompt_context
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
    

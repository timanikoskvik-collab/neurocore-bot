import asyncio
import os
import sqlite3
import urllib.parse
import time
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, BufferedInputFile, InlineKeyboardMarkup, 
    InlineKeyboardButton, CallbackQuery, PreCheckoutQuery, LabeledPrice
)
from google import genai

# Токен Telegram (проверяем обе переменные окружения для надежности)
TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Модель Gemini (по умолчанию gemini-3.6-flash согласно ответу Google API)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

if not TOKEN:
    raise ValueError("Не найден TELEGRAM_TOKEN или BOT_TOKEN в переменных окружения!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Инициализация клиента Gemini
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

FREE_IMAGE_LIMIT = 3
RESET_TIME_SECONDS = 86400  # 24 часа


# ==========================================
# 1. БАЗА ДАННЫХ И ЛИМИТЫ (С ТАЙМЕРОМ)
# ==========================================
def init_db():
    conn = sqlite3.connect("nco_database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            tier TEXT DEFAULT 'free',
            images_used INTEGER DEFAULT 0,
            last_reset INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_user_data(user_id: int):
    conn = sqlite3.connect("nco_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT tier, images_used, last_reset FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    now = int(time.time())
    
    if not row:
        cursor.execute("INSERT INTO users (user_id, tier, images_used, last_reset) VALUES (?, 'free', 0, ?)", (user_id, now))
        conn.commit()
        conn.close()
        return 'free', 0

    tier, images_used, last_reset = row
    
    # Сброс лимитов каждые 24 часа
    if now - last_reset >= RESET_TIME_SECONDS:
        images_used = 0
        cursor.execute("UPDATE users SET images_used = 0, last_reset = ? WHERE user_id = ?", (now, user_id))
        conn.commit()
        
    conn.close()
    return tier, images_used

def increment_user_images(user_id: int):
    conn = sqlite3.connect("nco_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET images_used = images_used + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def update_user_tier(user_id: int, new_tier: str):
    conn = sqlite3.connect("nco_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET tier = ? WHERE user_id = ?", (new_tier, user_id))
    conn.commit()
    conn.close()

def save_message_to_db(user_id: int, role: str, content: str):
    conn = sqlite3.connect("nco_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
    conn.commit()
    conn.close()

def get_chat_history(user_id: int, limit: int = 10):
    conn = sqlite3.connect("nco_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return list(reversed(rows))


# ==========================================
# 2. ЗАЩИТА ОТ ЗАСЫПАНИЯ НА RENDER
# ==========================================
async def handle_index(request):
    return web.Response(text="NeuroCore Omega (NCO) System is running.")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_index)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def self_ping_task():
    await asyncio.sleep(30)
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if not render_url:
        return
    if not render_url.startswith("http"):
        render_url = f"https://{render_url}"

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(render_url, timeout=10):
                    pass
            except Exception:
                pass
            await asyncio.sleep(240)


# ==========================================
# 3. ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ
# ==========================================
async def generate_image(prompt: str) -> bytes | None:
    encoded_prompt = urllib.parse.quote(prompt.strip())
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(image_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=35) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception as e:
            print(f"❌ Ошибка генерации фото: {e}")
        return None


# ==========================================
# 4. КОМАНДЫ И ПОДПИСКИ (TELEGRAM STARS)
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    tier, images_used = get_user_data(message.from_user.id)
    text = (
        "⚡ **NEUROCORE OMEGA (NCO v3.4)**\n\n"
        f"👤 Ваш статус: **{tier.upper()}**\n"
        f"🎨 Сгенерировано (Free): **{images_used}/{FREE_IMAGE_LIMIT}** (обновление каждые 24ч)\n\n"
        "• `/draw <описание>` — сгенерировать картинку\n"
        "• `/premium` — купить PRO подписку за Stars"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("premium"))
async def cmd_premium(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Месяц - 25 Stars", callback_data="buy_1")],
        [InlineKeyboardButton(text="⭐ 3 Месяца - 70 Stars", callback_data="buy_3")],
        [InlineKeyboardButton(text="⭐ 6 Месяцев - 130 Stars", callback_data="buy_6")],
        [InlineKeyboardButton(text="⭐ 12 Месяцев - 250 Stars", callback_data="buy_12")],
        [InlineKeyboardButton(text="⭐ 24 Месяца - 450 Stars", callback_data="buy_24")]
    ])
    await message.answer(
        "💎 **NCO PRO Режим**\n\nСнимает все лимиты на генерацию изображений и дает приоритет при общении с нейросетью.\n\nВыберите период подписки:", 
        reply_markup=keyboard, 
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy_callback(callback: CallbackQuery):
    months = int(callback.data.split("_")[1])
    prices_stars = {1: 25, 3: 70, 6: 130, 12: 250, 24: 450}
    stars_amount = prices_stars.get(months, 25)
    
    prices = [LabeledPrice(label=f"PRO Подписка ({months} мес.)", amount=stars_amount)]
    
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=f"Подписка NCO PRO ({months} мес.)",
        description="Безлимитная генерация картинок и приоритетный доступ.",
        payload=f"pro_{months}_months",
        provider_token="", # Для Telegram Stars оставляем пустым
        currency="XTR",
        prices=prices
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    update_user_tier(message.from_user.id, 'pro')
    await message.answer("✨ **Оплата прошла успешно!**\nРежим **PRO** активирован. Все ограничения сняты!", parse_mode="Markdown")

@dp.message(Command("draw"))
async def cmd_draw(message: Message):
    user_id = message.from_user.id
    tier, images_used = get_user_data(user_id)
    
    if tier == 'free' and images_used >= FREE_IMAGE_LIMIT:
        await message.answer("⚠️ **Лимит исчерпан!**\n3 бесплатные попытки обновляются раз в 24 часа. Снимите ограничения командой `/premium`.", parse_mode="Markdown")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "⚠️ Укажи текстовый запрос, например: `/draw cyberpunk city`\n\n"
            "❗️ *Важно: нейросеть лучше всего понимает запросы на английском языке!*", 
            parse_mode="Markdown"
        )
        return
    
    status_msg = await message.answer(f"⏳ Генерация арта ({tier.upper()} тариф)...")
    if tier == 'free': await asyncio.sleep(2)

    prompt = args[1]
    image_data = await generate_image(prompt)
    
    if image_data:
        increment_user_images(user_id)
        _, updated_used = get_user_data(user_id)
        
        photo = BufferedInputFile(image_data, filename="nco_art.jpg")
        caption = f"🎨 Запрос: {prompt}\n👤 Тариф: {tier.upper()}"
        if tier == 'free':
            caption += f"\n📊 Осталось попыток: {FREE_IMAGE_LIMIT - updated_used}"
            
        await message.answer_photo(photo=photo, caption=caption)
        try: await status_msg.delete()
        except: pass
    else:
        try: await status_msg.edit_text("⚠️ Ошибка генерации. Изображение не создано, лимит не списан.")
        except: pass


# ==========================================
# 5. ОБРАБОТЧИК ЧАТА С ИИ (ВСЕГДА В КОНЦЕ!)
# ==========================================
@dp.message()
async def handle_chat(message: Message):
    user_id = message.from_user.id
    tier, _ = get_user_data(user_id)
    user_text = message.text

    save_message_to_db(user_id, "user", user_text)
    history = get_chat_history(user_id, limit=6)
    
    response_text = "⚠️ Ошибка связи с Gemini. Проверь GEMINI_API_KEY."

    if gemini_client:
        try:
            formatted_history = ""
            for role, content in history:
                r_name = "Пользователь" if role == "user" else "Ассистент"
                formatted_history += f"{r_name}: {content}\n"
            
            full_prompt = (
                "Ты — умный ИИ-помощник NeuroCore Omega (NCO).\n"
                f"История диалога:\n{formatted_history}\n"
                f"Пользователь: {user_text}\n"
                "Ответь информативно и вежливо на русском языке."
            )
            
            # Вызов актуальной модели Gemini
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=full_prompt,
            )
            if response and response.text:
                response_text = response.text
        except Exception as e:
            print(f"❌ Ошибка Gemini API ({GEMINI_MODEL}): {e}")
            response_text = f"⚠️ Ошибка Gemini ({GEMINI_MODEL}): {e}"

    save_message_to_db(user_id, "model", response_text)
    await message.answer(response_text, parse_mode="Markdown")


# ==========================================
# 6. ЗАПУСК
# ==========================================
async def main():
    asyncio.create_task(start_web_server())
    asyncio.create_task(self_ping_task())
    print(f"NCO v3.4 успешно запущен! Используется модель: {GEMINI_MODEL}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

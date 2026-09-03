import asyncio
import os
import sqlite3
import urllib.parse
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from google import genai

# Инициализация переменных окружения с учетом TELEGRAM_TOKEN
TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TOKEN:
    raise ValueError("Не найден TELEGRAM_TOKEN!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# ==========================================
# 1. БАЗА ДАННЫХ (История чатов и Про-статус)
# ==========================================
def init_db():
    conn = sqlite3.connect("nco_database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            tier TEXT DEFAULT 'free'
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

def get_user_tier(user_id: int) -> str:
    conn = sqlite3.connect("nco_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT tier FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO users (user_id, tier) VALUES (?, 'free')", (user_id,))
        conn.commit()
        conn.close()
        return 'free'
    conn.close()
    return row[0]

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
    return web.Response(text="NeuroCore Omega (NCO) System is online.")

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
                async with session.get(render_url, timeout=10) as resp:
                    pass
            except Exception:
                pass
            await asyncio.sleep(240)


# ==========================================
# 3. ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ (Pollinations)
# ==========================================
async def generate_image(prompt: str) -> bytes | None:
    clean_prompt = prompt.strip()
    encoded_prompt = urllib.parse.quote(clean_prompt)
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
    
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(image_url, headers=headers, timeout=35) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception as e:
            print(f"Image generation error: {e}")
        return None


# ==========================================
# 4. ОБРАБОТЧИКИ СООБЩЕНИЙ И КОМАНД
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    tier = get_user_tier(user_id)
    text = (
        "⚡ **NEUROCORE OMEGA (NCO v3.1)**\n\n"
        f"Ваш статус: **{tier.upper()}**\n"
        "• Контекст чата и история сохраняются автоматически в базе данных.\n"
        "• Генерация графики: `/draw <описание>`\n"
        "• Переключение в Pro-режим: `/pro`"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("pro"))
async def cmd_pro(message: Message):
    conn = sqlite3.connect("nco_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET tier = 'pro' WHERE user_id = ?", (message.from_user.id,))
    conn.commit()
    conn.close()
    await message.answer("✨ Режим **PRO** успешно активирован!", parse_mode="Markdown")

@dp.message(Command("draw"))
async def cmd_draw(message: Message):
    user_id = message.from_user.id
    tier = get_user_tier(user_id)
    
    if tier == 'free':
        status_msg = await message.answer("⏳ Free-режим: ожидание очереди...")
        await asyncio.sleep(4)
    else:
        status_msg = await message.answer("⚡ Pro-режим: мгновенная генерация концепта...")

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await status_msg.edit_text("⚠️ Укажи текстовый запрос, например: `/draw киберпанк город`", parse_mode="Markdown")
        return
    
    prompt = args[1]
    image_data = await generate_image(prompt)
    
    if image_data:
        photo = BufferedInputFile(image_data, filename="nco_art.jpg")
        await message.answer_photo(photo=photo, caption=f"🎨 Запрос: {prompt}\n👤 Тариф: {tier.upper()}")
        try:
            await status_msg.delete()
        except:
            pass
    else:
        try:
            await status_msg.edit_text("⚠️ Не удалось сгенерировать изображение.")
        except:
            await message.answer("⚠️ Не удалось сгенерировать изображение.")

@dp.message()
async def handle_chat(message: Message):
    user_id = message.from_user.id
    tier = get_user_tier(user_id)
    user_text = message.text

    save_message_to_db(user_id, "user", user_text)
    history = get_chat_history(user_id, limit=6)
    
    if tier == 'free':
        await asyncio.sleep(2)
        model_name = "Gemini Flash (Free)"
    else:
        model_name = "Gemini Flash (Pro)"

    response_text = f"🧠 **NCO Terminal [{model_name}]**:\nЗапрос обработан.\nВаш текст: {user_text}"
    
    if gemini_client:
        try:
            contents = "\n".join([f"{h[0]}: {h[1]}" for h in history])
            response = gemini_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=contents,
            )
            if response and response.text:
                response_text = response.text
        except Exception as e:
            print(f"Gemini API error: {e}")

    save_message_to_db(user_id, "model", response_text)
    await message.answer(response_text, parse_mode="Markdown")


# ==========================================
# 5. ЗАПУСК СИСТЕМЫ
# ==========================================
async def main():
    asyncio.create_task(start_web_server())
    asyncio.create_task(self_ping_task())
    
    print("NeuroCore Omega (NCO) запущен с поддержкой базы данных и защиты от сна!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

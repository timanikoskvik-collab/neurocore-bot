import aiosqlite
import time

DB_NAME = "bot_database.db"

# Константы лимитов (Максимальные значения)
FREE_LIMITS = {"msg": 40, "photo": 3, "draw": 1}
PRO_LIMITS = {"msg": 100, "photo": 20, "draw": 10}

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица пользователей (лимиты и время)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                is_premium INTEGER DEFAULT 0,
                msg_left INTEGER DEFAULT 40,
                photo_left INTEGER DEFAULT 3,
                draw_left INTEGER DEFAULT 1,
                last_full_reset REAL DEFAULT 0,
                last_hourly_bonus REAL DEFAULT 0
            )
        """)
        # Таблица истории чата для контекста
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp REAL
            )
        """)
        await db.commit()

async def check_and_update_limits(user_id: int):
    """Проверяет время и обновляет лимиты (полный сброс раз в 24ч, бонусы раз в час)"""
    current_time = time.time()
    
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            
            if not user:
                # Новый пользователь
                await db.execute(
                    "INSERT INTO users (user_id, msg_left, photo_left, draw_left, last_full_reset, last_hourly_bonus) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, FREE_LIMITS["msg"], FREE_LIMITS["photo"], FREE_LIMITS["draw"], current_time, current_time)
                )
                await db.commit()
                return await get_user(user_id)

            # Проверяем, прошло ли 24 часа (86400 секунд)
            if current_time - user['last_full_reset'] >= 86400:
                is_prem = bool(user['is_premium'])
                limits = PRO_LIMITS if is_prem else FREE_LIMITS
                await db.execute(
                    """UPDATE users SET msg_left = ?, photo_left = ?, draw_left = ?, 
                       last_full_reset = ?, last_hourly_bonus = ? WHERE user_id = ?""",
                    (limits["msg"], limits["photo"], limits["draw"], current_time, current_time, user_id)
                )
                await db.commit()
                return await get_user(user_id)
            
            # Проверяем, прошел ли 1 час (3600 секунд) для выдачи бонуса, если лимиты на нуле
            if current_time - user['last_hourly_bonus'] >= 3600:
                is_prem = bool(user['is_premium'])
                limits = PRO_LIMITS if is_prem else FREE_LIMITS
                
                # Добавляем по 2 сообщения и 1 фото каждый час (но не больше максимума)
                new_msg = min(user['msg_left'] + 2, limits["msg"])
                new_photo = min(user['photo_left'] + 1, limits["photo"])
                new_draw = min(user['draw_left'] + 1, limits["draw"])
                
                await db.execute(
                    """UPDATE users SET msg_left = ?, photo_left = ?, draw_left = ?, last_hourly_bonus = ? WHERE user_id = ?""",
                    (new_msg, new_photo, new_draw, current_time, user_id)
                )
                await db.commit()

async def get_user(user_id: int):
    """Получает пользователя, предварительно обновив его лимиты по времени"""
    await check_and_update_limits(user_id)
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            return dict(user)

async def decrement_limit(user_id: int, limit_type: str):
    """Списывает лимит ТОЛЬКО после успешной генерации. limit_type: 'msg', 'photo' или 'draw'"""
    column = f"{limit_type}_left"
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"UPDATE users SET {column} = {column} - 1 WHERE user_id = ? AND {column} > 0", (user_id,))
        await db.commit()

async def set_premium(user_id: int, status: bool = True):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET is_premium = ? WHERE user_id = ?", (1 if status else 0, user_id))
        await db.commit()
    # Сразу обновляем лимиты по новому статусу
    await check_and_update_limits(user_id)

# --- ИСТОРИЯ ЧАТА (ПАМЯТЬ КОНТЕКСТА) ---

async def add_message(user_id: int, role: str, content: str):
    """Сохраняет сообщение в базу"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, role, content, time.time())
        )
        await db.commit()

async def get_history(user_id: int, limit: int = 10):
    """Возвращает последние N сообщений для контекста"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Берем последние `limit` сообщений, отсортированные по времени
        async with db.execute(
            "SELECT role, content FROM (SELECT * FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            
            history = []
            for role, content in rows:
                history.append({"role": role, "parts": [{"text": content}]})
            return history

async def clear_history(user_id: int):
    """Полностью очищает память диалога для пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        await db.commit()

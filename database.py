import aiosqlite
import time

DB_NAME = "bot_database.db"

FREE_LIMITS = {"msg": 40, "photo": 3, "draw": 1}
PRO_LIMITS = {"msg": 100, "photo": 20, "draw": 10}

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                is_premium INTEGER DEFAULT 0,
                msg_left INTEGER DEFAULT 40,
                photo_left INTEGER DEFAULT 3,
                draw_left INTEGER DEFAULT 1,
                active_session INTEGER DEFAULT 1,
                last_full_reset REAL DEFAULT 0,
                last_hourly_bonus REAL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                session_id INTEGER DEFAULT 1,
                role TEXT,
                content TEXT,
                timestamp REAL
            )
        """)
        await db.commit()

async def check_and_update_limits(user_id: int):
    current_time = time.time()
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                await db.execute(
                    "INSERT INTO users (user_id, msg_left, photo_left, draw_left, active_session, last_full_reset, last_hourly_bonus) VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (user_id, FREE_LIMITS["msg"], FREE_LIMITS["photo"], FREE_LIMITS["draw"], current_time, current_time)
                )
                await db.commit()
                return await get_user(user_id)

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
            
            if current_time - user['last_hourly_bonus'] >= 3600:
                is_prem = bool(user['is_premium'])
                limits = PRO_LIMITS if is_prem else FREE_LIMITS
                new_msg = min(user['msg_left'] + 2, limits["msg"])
                new_photo = min(user['photo_left'] + 1, limits["photo"])
                new_draw = min(user['draw_left'] + 1, limits["draw"])
                await db.execute(
                    """UPDATE users SET msg_left = ?, photo_left = ?, draw_left = ?, last_hourly_bonus = ? WHERE user_id = ?""",
                    (new_msg, new_photo, new_draw, current_time, user_id)
                )
                await db.commit()

async def get_user(user_id: int):
    await check_and_update_limits(user_id)
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            return dict(user)

async def set_active_session(user_id: int, session_id: int):
    """Смена текущей ветки чата"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET active_session = ? WHERE user_id = ?", (session_id, user_id))
        await db.commit()

async def decrement_limit(user_id: int, limit_type: str):
    column = f"{limit_type}_left"
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"UPDATE users SET {column} = {column} - 1 WHERE user_id = ? AND {column} > 0", (user_id,))
        await db.commit()

async def set_premium(user_id: int, status: bool = True):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET is_premium = ? WHERE user_id = ?", (1 if status else 0, user_id))
        await db.commit()
    await check_and_update_limits(user_id)

async def add_message(user_id: int, role: str, content: str, session_id: int = 1):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO history (user_id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_id, session_id, role, content, time.time())
        )
        await db.commit()

async def get_history(user_id: int, session_id: int = 1, limit: int = 10):
    """Возвращает историю конкретного чата"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT role, content FROM (SELECT * FROM history WHERE user_id = ? AND session_id = ? ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC",
            (user_id, session_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"role": role, "parts": [{"text": content}]} for role, content in rows]

async def clear_history(user_id: int, session_id: int = 1):
    """Очищает историю только текущего чата"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM history WHERE user_id = ? AND session_id = ?", (user_id, session_id))
        await db.commit()

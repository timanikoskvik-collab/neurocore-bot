import aiosqlite

DB_NAME = "bot_database.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                is_premium INTEGER DEFAULT 0,
                msg_count INTEGER DEFAULT 0,
                photo_count INTEGER DEFAULT 0,
                last_reset_date TEXT
            )
        """)
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                await db.execute(
                    "INSERT INTO users (user_id, is_premium, msg_count, photo_count) VALUES (?, 0, 0, 0)",
                    (user_id,)
                )
                await db.commit()
                async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as new_cursor:
                    user = await new_cursor.fetchone()
            return dict(user)

async def increment_usage(user_id: int, is_photo: bool = False):
    async with aiosqlite.connect(DB_NAME) as db:
        if is_photo:
            await db.execute("UPDATE users SET photo_count = photo_count + 1 WHERE user_id = ?", (user_id,))
        else:
            await db.execute("UPDATE users SET msg_count = msg_count + 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def set_premium(user_id: int, status: bool = True):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET is_premium = ? WHERE user_id = ?", (1 if status else 0, user_id))
        await db.commit()
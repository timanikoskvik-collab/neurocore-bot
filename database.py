import os
import time
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId

# Настройки лимитов
LIMITS = {
    "free": {"text": 40, "photo": 3, "draw": 1},
    "pro": {"text": 100, "photo": 30, "draw": 10}
}

class Database:
    def __init__(self):
        self.client = AsyncIOMotorClient(os.environ.get("MONGO_URI"))
        self.db = self.client.neurocore_db
        self.users = self.db.users
        self.chats = self.db.chats

    async def get_user(self, user_id: int):
        user = await self.users.find_one({"user_id": user_id})
        current_time = int(time.time())
        
        if not user:
            user = {
                "user_id": user_id,
                "tier": "free",
                "text_limit": LIMITS["free"]["text"],
                "photo_limit": LIMITS["free"]["photo"],
                "draw_limit": LIMITS["free"]["draw"],
                "last_reset": current_time,
                "current_chat_id": None
            }
            await self.users.insert_one(user)
        else:
            # Проверка на сброс лимитов (86400 сек = 24 часа)
            if current_time - user.get("last_reset", 0) > 86400:
                tier = user.get("tier", "free")
                await self.users.update_one(
                    {"user_id": user_id},
                    {"$set": {
                        "text_limit": LIMITS[tier]["text"],
                        "photo_limit": LIMITS[tier]["photo"],
                        "draw_limit": LIMITS[tier]["draw"],
                        "last_reset": current_time
                    }}
                )
                user = await self.users.find_one({"user_id": user_id})
        return user

    async def decrement_limit(self, user_id: int, limit_type: str):
        await self.users.update_one(
            {"user_id": user_id},
            {"$inc": {f"{limit_type}_limit": -1}}
        )

    async def upgrade_to_pro(self, user_id: int):
        await self.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "tier": "pro",
                "text_limit": LIMITS["pro"]["text"],
                "photo_limit": LIMITS["pro"]["photo"],
                "draw_limit": LIMITS["pro"]["draw"],
                "last_reset": int(time.time())
            }}
        )

    async def create_chat(self, user_id: int, title: str):
        chat_doc = {
            "user_id": user_id,
            "title": title[:25],
            "messages": [],
            "updated_at": int(time.time())
        }
        result = await self.chats.insert_one(chat_doc)
        chat_id = str(result.inserted_id)
        await self.set_current_chat(user_id, chat_id)
        return chat_id

    async def get_chat(self, chat_id: str):
        if not chat_id:
            return None
        return await self.chats.find_one({"_id": ObjectId(chat_id)})

    async def add_message(self, chat_id: str, role: str, text: str):
        await self.chats.update_one(
            {"_id": ObjectId(chat_id)},
            {
                "$push": {"messages": {"role": role, "text": text}},
                "$set": {"updated_at": int(time.time())}
            }
        )

    async def get_recent_chats(self, user_id: int, limit=10):
        cursor = self.chats.find({"user_id": user_id}).sort("updated_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def set_current_chat(self, user_id: int, chat_id: str | None):
        await self.users.update_one(
            {"user_id": user_id},
            {"$set": {"current_chat_id": chat_id}}
        )

# Глобальный экземпляр для импорта
db = Database()

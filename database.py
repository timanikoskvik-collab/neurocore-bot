import os
import time
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
client = AsyncIOMotorClient(MONGO_URI)
db = client["neurocore_db"]

users_col = db["users"]
chats_col = db["chats"]

TIER_LIMITS = {
    "free": {"text": 40, "photo": 3, "draw": 1},
    "pro": {"text": 100, "photo": 30, "draw": 10}
}


async def get_or_create_user(user_id: int, username: Optional[str] = None) -> Dict[str, Any]:

    now = time.time()
    user = await users_col.find_one({"_id": user_id})

    if not user:
        user = {
            "_id": user_id,
            "username": username,
            "tier": "free",
            "last_reset": now,
            "text_count": 0,
            "photo_count": 0,
            "draw_count": 0,
            "current_chat_id": None
        }
        await users_col.insert_one(user)
        return user

    if now - user.get("last_reset", 0) >= 86400:
        await users_col.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "last_reset": now,
                    "text_count": 0,
                    "photo_count": 0,
                    "draw_count": 0
                }
            }
        )
        user["last_reset"] = now
        user["text_count"] = 0
        user["photo_count"] = 0
        user["draw_count"] = 0

    return user


async def check_and_increment_limit(user_id: int, limit_type: str) -> tuple[bool, int, int]:

    user = await get_or_create_user(user_id)
    tier = user.get("tier", "free")
    max_limit = TIER_LIMITS.get(tier, TIER_LIMITS["free"]).get(limit_type, 0)
    current_count = user.get(f"{limit_type}_count", 0)

    if current_count >= max_limit:
        return False, current_count, max_limit

    await users_col.update_one(
        {"_id": user_id},
        {"$inc": {f"{limit_type}_count": 1}}
    )
    return True, current_count + 1, max_limit


async def set_user_tier(user_id: int, tier: str) -> None:

    await users_col.update_one(
        {"_id": user_id},
        {"$set": {"tier": tier}}
    )


async def get_current_chat_id(user_id: int) -> Optional[str]:

    user = await get_or_create_user(user_id)
    return user.get("current_chat_id")


async def set_current_chat_id(user_id: int, chat_id: Optional[str]) -> None:

    await users_col.update_one(
        {"_id": user_id},
        {"$set": {"current_chat_id": chat_id}}
    )


async def create_chat_room(user_id: int, first_message_text: str) -> str:

    title = first_message_text[:25].strip() if first_message_text else "Новый диалог"
    now = time.time()
    
    new_chat = {
        "user_id": user_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "messages": []
    }
    result = await chats_col.insert_one(new_chat)
    chat_id = str(result.inserted_id)
    
    await set_current_chat_id(user_id, chat_id)
    return chat_id


async def append_message_to_chat(chat_id: str, role: str, text: str) -> None:

    try:
        obj_id = ObjectId(chat_id)
        await chats_col.update_one(
            {"_id": obj_id},
            {
                "$push": {"messages": {"role": role, "text": text}},
                "$set": {"updated_at": time.time()}
            }
        )
    except Exception:
        pass


async def get_chat_history(chat_id: str) -> List[Dict[str, str]]:

    try:
        chat = await chats_col.find_one({"_id": ObjectId(chat_id)})
        return chat.get("messages", []) if chat else []
    except Exception:
        return []


async def get_recent_chats(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:

    cursor = chats_col.find({"user_id": user_id}).sort("updated_at", -1).limit(limit)
    chats = []
    async for chat in cursor:
        chats.append({
            "id": str(chat["_id"]),
            "title": chat.get("title", "Без названия")
        })
    return chats

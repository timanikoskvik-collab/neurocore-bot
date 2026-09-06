import os
import time
from typing import Dict, Any, List, Optional
from bson.objectid import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
client = AsyncIOMotorClient(MONGO_URI)
db = client["neurocore_db"]

users_collection = db["users"]
chats_collection = db["chats"]

TIERS = {
    "free": {
        "text_limit": 40,
        "photo_limit": 3,
        "draw_limit": 5,
        "genai_model": "gemini-3.5-flash",
        "model_name": "NCO 2.4 (Free)"
    },
    "pro": {
        "text_limit": 200,
        "photo_limit": 30,
        "draw_limit": 20,
        "genai_model": "gemini-3.7-flash",
        "model_name": "NCO 3.1 (PRO)"
    }
}

async def get_or_create_user(user_id: int, username: str = None) -> Dict[str, Any]:
    user = await users_collection.find_one({"user_id": user_id})
    now = time.time()

    if not user:
        user = {
            "user_id": user_id,
            "username": username,
            "tier": "free",
            "subscription_expires": 0,
            "text_count": 0,
            "photo_count": 0,
            "draw_count": 0,
            "last_reset": now,
            "current_chat_id": None
        }
        await users_collection.insert_one(user)
    else:
        if user.get("tier") == "pro" and user.get("subscription_expires", 0) < now:
            await users_collection.update_one(
                {"user_id": user_id},
                {"$set": {"tier": "free"}}
            )
            user["tier"] = "free"

        if now - user.get("last_reset", 0) > 86400:
            await users_collection.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "text_count": 0,
                        "photo_count": 0,
                        "draw_count": 0,
                        "last_reset": now
                    }
                }
            )
            user["text_count"] = 0
            user["photo_count"] = 0
            user["draw_count"] = 0

    return user

async def check_and_increment_limit(user_id: int, limit_type: str) -> bool:
    user = await get_or_create_user(user_id)
    tier = user.get("tier", "free")
    tier_info = TIERS.get(tier, TIERS["free"])

    field_map = {
        "text": ("text_count", tier_info["text_limit"]),
        "photo": ("photo_count", tier_info["photo_limit"]),
        "draw": ("draw_count", tier_info["draw_limit"])
    }

    if limit_type not in field_map:
        return False

    count_field, max_limit = field_map[limit_type]
    current_count = user.get(count_field, 0)

    if current_count >= max_limit:
        return False

    await users_collection.update_one(
        {"user_id": user_id},
        {"$inc": {count_field: 1}}
    )
    return True

async def activate_pro_subscription(user_id: int, months: int):
    user = await get_or_create_user(user_id)
    now = time.time()
    current_expires = user.get("subscription_expires", 0)

    if current_expires > now:
        new_expires = current_expires + (months * 30 * 86400)
    else:
        new_expires = now + (months * 30 * 86400)

    await users_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "tier": "pro",
                "subscription_expires": new_expires
            }
        }
    )

async def create_new_chat(user_id: int, first_message: str) -> str:
    title = first_message[:30] + "..." if len(first_message) > 30 else first_message
    chat_data = {
        "user_id": user_id,
        "title": title,
        "created_at": time.time(),
        "messages": []
    }
    result = await chats_collection.insert_one(chat_data)
    chat_id = str(result.inserted_id)

    await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"current_chat_id": chat_id}}
    )
    return chat_id

async def set_current_chat(user_id: int, chat_id: Optional[str]):
    await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"current_chat_id": chat_id}}
    )

async def add_message_to_chat(chat_id: str, role: str, content: str):
    try:
        await chats_collection.update_one(
            {"_id": ObjectId(chat_id)},
            {"$push": {"messages": {"role": role, "content": content, "timestamp": time.time()}}}
        )
    except Exception:
        pass

async def get_chat_history(chat_id: str, limit: int = 10) -> List[Dict[str, str]]:
    try:
        chat = await chats_collection.find_one({"_id": ObjectId(chat_id)})
        if chat and "messages" in chat:
            return chat["messages"][-limit:]
    except Exception:
        pass
    return []

async def get_user_recent_chats(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    cursor = chats_collection.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
    chats = []
    async for doc in cursor:
        chats.append({
            "id": str(doc["_id"]),
            "title": doc.get("title", "Диалог"),
            "created_at": doc.get("created_at")
        })
    return chats
    

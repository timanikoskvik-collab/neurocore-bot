import os
import time
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId

# Подключение к облаку через переменную окружения на Render
MONGO_URI = os.getenv("MONGO_URI")
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["neurocore_omega_db"]

users_col = db["users"]
chats_col = db["chats"]

async def get_user_data(user_id: int):
    """Загружает профиль и автоматически сбрасывает суточные лимиты через 24 часа"""
    user = await users_col.find_one({"user_id": user_id})
    now = int(time.time())
    
    if not user:
        user = {
            "user_id": user_id,
            "tier": "free",
            "current_chat_id": None,
            "messages_used": 0,
            "images_used": 0,
            "last_reset": now
        }
        await users_col.insert_one(user)
        return user

    # Если прошло 24 часа (86400 секунд), сбрасываем счетчики в 0
    if now - user.get("last_reset", 0) >= 86400:
        await users_col.update_one(
            {"user_id": user_id},
            {"$set": {"messages_used": 0, "images_used": 0, "last_reset": now}}
        )
        user = await users_col.find_one({"user_id": user_id})
        
    return user

async def create_new_chat(user_id: int, first_message: str):
    """Создает новую комнату чата с автоматическим названием из первого сообщения"""
    title = first_message[:25] + "..." if len(first_message) > 25 else first_message
    new_chat = {
        "user_id": user_id,
        "title": title,
        "messages": [],
        "updated_at": datetime.utcnow()
    }
    res = await chats_col.insert_one(new_chat)
    chat_id = res.inserted_id
    # Делаем созданный чат активным для пользователя
    await users_col.update_one({"user_id": user_id}, {"$set": {"current_chat_id": chat_id}})
    return chat_id

async def get_chat_history(chat_id):
    """Вытаскивает историю сообщений конкретной комнаты"""
    if not chat_id:
        return []
    chat = await chats_col.find_one({"_id": ObjectId(chat_id)})
    return chat.get("messages", []) if chat else []

async def save_chat_step(chat_id, user_id, user_text, ai_text):
    """Сохраняет реплики в историю комнаты и накручивает счетчик сообщений"""
    await chats_col.update_one(
        {"_id": ObjectId(chat_id)},
        {
            "$push": {
                "messages": {
                    "$each": [
                        {"role": "user", "text": user_text},
                        {"role": "model", "text": ai_text}
                    ]
                }
            },
            "$set": {"updated_at": datetime.utcnow()}
        }
    )
    await users_col.update_one({"user_id": user_id}, {"$inc": {"messages_used": 1}})

async def get_recent_chats(user_id: int):
    """Выдает топ-10 последних чатов юзера для вывода кнопок меню (работает мгновенно)"""
    cursor = chats_col.find({"user_id": user_id}).sort("updated_at", -1).limit(10)
    return await cursor.to_list(length=10)

async def set_active_chat(user_id: int, chat_id_str: str):
    """Переключает текущую активную комнату пользователя"""
    await users_col.update_one({"user_id": user_id}, {"$set": {"current_chat_id": ObjectId(chat_id_str)}})

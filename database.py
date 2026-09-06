import os
from datetime import datetime, timedelta
from pymongo import MongoClient

# Подключение к MongoDB Atlas (переменная MONGO_URI на Render)
db_client = MongoClient(os.environ.get("MONGO_URI"))
db = db_client["neuro_bot_db"]
users_col = db["users"]
chats_col = db["chats"]

def get_user_profile(user_id):
    """Получает профиль пользователя и сбрасывает суточные лимиты, если прошло 24 часа"""
    user = users_col.find_one({"user_id": user_id})
    if not user:
        user = {
            "user_id": user_id,
            "status": "free",  # 'free' или 'pro'
            "current_chat_id": None,
            "last_reset": datetime.utcnow(),
            "usage": {"messages": 0, "photos_in": 0, "photos_gen": 0}
        }
        users_col.insert_one(user)
    
    # Сброс лимитов раз в 24 часа
    if datetime.utcnow() - user["last_reset"] >= timedelta(days=1):
        users_col.update_one(
            {"user_id": user_id},
            {"$set": {
                "usage": {"messages": 0, "photos_in": 0, "photos_gen": 0},
                "last_reset": datetime.utcnow()
            }}
        )
        user = users_col.find_one({"user_id": user_id})
    return user

def create_new_chat(user_id, first_message_text):
    """Автоматически создает новый чат с коротким заголовком из первого сообщения"""
    from bson.objectid import ObjectId
    title = first_message_text[:25] + "..." if len(first_message_text) > 25 else first_message_text
    new_chat_doc = {
        "user_id": user_id,
        "title": title,
        "messages": [],
        "updated_at": datetime.utcnow()
    }
    insert_result = chats_col.insert_one(new_chat_doc)
    chat_id = insert_result.inserted_id
    # Делаем этот чат активным для юзера
    users_col.update_one({"user_id": user_id}, {"$set": {"current_chat_id": chat_id}})
    return chat_id

def get_active_chat_history(chat_id):
    """Загружает историю сообщений конкретного чата"""
    from bson.objectid import ObjectId
    chat = chats_col.find_one({"_id": ObjectId(chat_id)})
    return chat.get("messages", []) if chat else []

def save_message_to_history(chat_id, user_id, user_text, ai_response):
    """Записывает реплики пользователя и ИИ в историю чата и увеличивает счетчик"""
    from bson.objectid import ObjectId
    chats_col.update_one(
        {"_id": ObjectId(chat_id)},
        {
            "$push": {
                "messages": {
                    "$each": [
                        {"role": "user", "text": user_text},
                        {"role": "model", "text": ai_response}
                    ]
                }
            },
            "$set": {"updated_at": datetime.utcnow()}
        }
    )
    users_col.update_one({"user_id": user_id}, {"$inc": {"usage.messages": 1}})

def get_recent_chats(user_id, limit=10):
    """Вытаскивает последние чаты пользователя для списка"""
    return list(chats_col.find({"user_id": user_id}).sort("updated_at", -1).limit(limit))

def switch_chat(user_id, chat_id_str):
    """Переключает активный чат пользователя"""
    from bson.objectid import ObjectId
    users_col.update_one({"user_id": user_id}, {"$set": {"current_chat_id": ObjectId(chat_id_str)}})

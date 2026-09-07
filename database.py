# -*- coding: utf-8 -*-
"""
database.py — ВСЕ настройки бота + хранилище лимитов/подписки/памяти диалога.

Здесь два блока:
  1. Переменные окружения и константы (бренды, модели, лимиты, тарифы) —
     раньше это был отдельный config.py, теперь всё в одном файле по вашей просьбе.
  2. Класс Storage поверх MongoDB (или памяти процесса, если MONGO_URI не задан)
     и бизнес-логика: тариф пользователя, автосброс лимитов раз в 24 часа,
     списание лимита, активация PRO, чтение/запись текстовой истории диалога.
"""

import os
import sys
import time
import logging
from typing import Optional, Dict, Any, Tuple, List

logger = logging.getLogger("neurocore_omega.database")

# ---------------------------------------------------------------------------
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MONGO_URI = os.environ.get("MONGO_URI")
PORT = int(os.environ.get("PORT", "10000"))

if not TELEGRAM_TOKEN:
    logger.critical("Не найден токен бота. Задайте TELEGRAM_TOKEN (или TELEGRAM_BOT_TOKEN) на Render.")
    sys.exit(1)
if not GEMINI_API_KEY:
    logger.critical("Не найден GEMINI_API_KEY на Render.")
    sys.exit(1)
if not MONGO_URI:
    logger.warning("MONGO_URI не задан — лимиты, подписка и память диалога будут храниться "
                    "только в памяти процесса и обнулятся при перезапуске/засыпании инстанса на Render.")

# ---------------------------------------------------------------------------
# БРЕНДИНГ
# ---------------------------------------------------------------------------
BOT_BRAND_NAME = "NeuroCore Omega | AI"
FREE_MODEL_BRAND = "NeuroCore Omega 2.4 Free"
PRO_MODEL_BRAND = "NeuroCore Omega 3.1 Pro"
VISION_MODEL_BRAND = "NeuroCore Vision 1.5"

# ---------------------------------------------------------------------------
# РЕАЛЬНЫЕ МОДЕЛИ GEMINI ЗА БРЕНДАМИ
# ---------------------------------------------------------------------------
TEXT_MODEL_BY_TIER = {
    "free": "gemini-3.5-flash",
    "pro": "gemini-3.7-flash",
}

# "Размышление" модели: по умолчанию Google ставит "high", что заметно увеличивает
# время ответа. "low"/"medium" — намного быстрее, без потери качества на обычных задачах.
THINKING_LEVEL_BY_TIER = {
    "free": "low",
    "pro": "medium",
}

IMAGE_MODEL_PRIMARY = "gemini-3.1-flash-image"
IMAGE_MODEL_FALLBACK = "gemini-3.1-flash-lite-image"

# ---------------------------------------------------------------------------
# ЛИМИТЫ (окно — 24 часа, обнуляется автоматически)
# ---------------------------------------------------------------------------
WINDOW_SECONDS = 24 * 60 * 60
LIMITS = {
    "free": {"messages": 30, "photos": 3, "images": 1},
    "pro": {"messages": 100, "photos": 30, "images": 10},
}

# ---------------------------------------------------------------------------
# ТАРИФЫ PRO (Telegram Stars, валюта XTR)
# ---------------------------------------------------------------------------
PRO_PLANS = [
    (1, 25, "1 месяц — 25 ⭐"),
    (3, 65, "3 месяца — 65 ⭐"),
    (6, 120, "6 месяцев — 120 ⭐"),
    (12, 220, "12 месяцев — 220 ⭐"),
    (24, 400, "24 месяца — 400 ⭐"),
]

# Сколько последних реплик (не токенов) хранить и передавать модели.
MAX_HISTORY_MESSAGES = 30


# ---------------------------------------------------------------------------
# ХРАНИЛИЩЕ: MongoDB (если есть MONGO_URI) либо память процесса (fallback)
# ---------------------------------------------------------------------------
class Storage:
    def __init__(self, mongo_uri: Optional[str]):
        self._memory: Dict[int, Dict[str, Any]] = {}
        self._collection = None
        if mongo_uri:
            try:
                from motor.motor_asyncio import AsyncIOMotorClient
                client = AsyncIOMotorClient(mongo_uri)
                self._collection = client["neurocore_omega"]["users"]
                logger.info("MongoDB подключена — лимиты, подписка и память диалога сохраняются постоянно.")
            except Exception as exc:  # noqa: BLE001
                logger.error("Не удалось подключиться к MongoDB (%s), работаю на памяти процесса.", exc)
                self._collection = None

    @staticmethod
    def _default_doc(user_id: int) -> Dict[str, Any]:
        return {
            "user_id": user_id,
            "is_pro": False,
            "pro_expires_at": None,
            "window_start": time.time(),
            "messages_used": 0,
            "photos_used": 0,
            "images_used": 0,
            "history": [],  # [{"role": "user"/"model", "text": "..."}]
        }

    async def get_user(self, user_id: int) -> Dict[str, Any]:
        if self._collection is not None:
            doc = await self._collection.find_one({"user_id": user_id})
            if doc is None:
                doc = self._default_doc(user_id)
                await self._collection.insert_one(dict(doc))
            doc.setdefault("history", [])
            return doc
        return self._memory.setdefault(user_id, self._default_doc(user_id))

    async def update_user(self, user_id: int, updates: Dict[str, Any]) -> None:
        if self._collection is not None:
            await self._collection.update_one({"user_id": user_id}, {"$set": updates}, upsert=True)
        else:
            self._memory.setdefault(user_id, self._default_doc(user_id)).update(updates)


storage = Storage(MONGO_URI)


# ---------------------------------------------------------------------------
# ПАМЯТЬ ДИАЛОГА
# ---------------------------------------------------------------------------
async def get_history(user_id: int) -> List[Dict[str, str]]:
    """Возвращает текстовую историю диалога пользователя (последние N реплик)."""
    user = await storage.get_user(user_id)
    return user.get("history", [])[-MAX_HISTORY_MESSAGES:]


async def append_history(user_id: int, user_text: str, model_text: str) -> None:
    """Добавляет пару "вопрос-ответ" в историю и обрезает её до лимита."""
    history = await get_history(user_id)
    history = history + [
        {"role": "user", "text": user_text},
        {"role": "model", "text": model_text},
    ]
    history = history[-MAX_HISTORY_MESSAGES:]
    await storage.update_user(user_id, {"history": history})


async def clear_history(user_id: int) -> None:
    await storage.update_user(user_id, {"history": []})


# ---------------------------------------------------------------------------
# БИЗНЕС-ЛОГИКА ТАРИФОВ И ЛИМИТОВ
# ---------------------------------------------------------------------------
def effective_tier(user: Dict[str, Any]) -> str:
    if user.get("is_pro") and user.get("pro_expires_at") and user["pro_expires_at"] > time.time():
        return "pro"
    return "free"


async def ensure_fresh_window(user_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    if now - user.get("window_start", 0) >= WINDOW_SECONDS:
        updates = {"window_start": now, "messages_used": 0, "photos_used": 0, "images_used": 0}
        user.update(updates)
        await storage.update_user(user_id, updates)
    return user


def time_left_str(user: Dict[str, Any]) -> str:
    remaining = max(0.0, WINDOW_SECONDS - (time.time() - user.get("window_start", time.time())))
    hours, minutes = int(remaining // 3600), int((remaining % 3600) // 60)
    return f"{hours} ч {minutes} мин"


async def try_consume(user_id: int, kind: str) -> Tuple[bool, Dict[str, Any], str]:
    """
    Пытается списать одну единицу лимита (kind: messages/photos/images).
    Возвращает (разрешено?, документ пользователя, текущий тариф).
    """
    user = await storage.get_user(user_id)
    user = await ensure_fresh_window(user_id, user)

    tier = effective_tier(user)
    if user.get("is_pro") and tier == "free":
        await storage.update_user(user_id, {"is_pro": False})
        user["is_pro"] = False

    limit = LIMITS[tier][kind]
    used_field = f"{kind}_used"
    used = user.get(used_field, 0)

    if used >= limit:
        return False, user, tier

    used += 1
    await storage.update_user(user_id, {used_field: used})
    user[used_field] = used
    return True, user, tier


async def activate_pro(user_id: int, months: int) -> float:
    """Активирует/продлевает PRO. Возвращает unix-время новой даты окончания."""
    user = await storage.get_user(user_id)
    now = time.time()
    base = user["pro_expires_at"] if (user.get("is_pro") and user.get("pro_expires_at", 0) > now) else now
    new_expiry = base + months * 30 * 24 * 60 * 60
    await storage.update_user(user_id, {"is_pro": True, "pro_expires_at": new_expiry})
    return new_expiry

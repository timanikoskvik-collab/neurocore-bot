#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===========================================================================
  NeuroCore Omega | AI — Telegram-бот на aiogram 3.x + aiohttp + google-genai
===========================================================================

Что внутри:
  - Текст и зрение: модели Gemini 3.5 Flash (Free) и Gemini 3.7 Flash (Pro).
  - Генерация картинок: Gemini 3.1 Flash Image ("NeuroCore Vision 1.5"),
    с автопереключением на облегчённую модель при сбое.
  - Лимиты на 24 часа: Free — 30 сообщений / 3 фото / 1 генерация,
    Pro — 100 сообщений / 30 фото / 10 генераций.
  - Оплата PRO через Telegram Stars (валюта XTR) на 1/3/6/12/24 месяца.
  - Хранение лимитов и подписки в MongoDB (переживает рестарт/сон Render).
    Если MONGO_URI не задан — работает на памяти процесса (для теста).
  - aiohttp health-check сервер (обязателен для Render Web Service).

ВАЖНО про переменные окружения на Render:
  TELEGRAM_TOKEN   — токен бота (поддерживается и старое имя TELEGRAM_BOT_TOKEN)
  GEMINI_API_KEY   — ключ Google GenAI
  MONGO_URI        — строка подключения MongoDB (опционально, но настоятельно рекомендуется)
  PORT             — порт для health-check (Render подставляет сам)
===========================================================================
"""

import os
import sys
import time
import asyncio
import logging
import traceback
from collections import defaultdict, deque
from typing import Optional, Dict, Any, List, Tuple

from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    LabeledPrice, PreCheckoutQuery,
)
from aiogram.exceptions import TelegramAPIError

from google import genai
from google.genai import types as genai_types
from google.genai.errors import APIError as GenAIAPIError

# ---------------------------------------------------------------------------------
# ЛОГИРОВАНИЕ
# ---------------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logger = logging.getLogger("neurocore_omega")

# ---------------------------------------------------------------------------------
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ---------------------------------------------------------------------------------
# Токен бота ищем сразу под двумя именами — это защищает от ситуации, когда
# на Render переменная называется иначе, чем ожидает код (частая причина
# падений при деплое: "Exited with status 1" из-за несовпадения имени).
TELEGRAM_TOKEN: Optional[str] = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY: Optional[str] = os.environ.get("GEMINI_API_KEY")
MONGO_URI: Optional[str] = os.environ.get("MONGO_URI")  # опционально
PORT: int = int(os.environ.get("PORT", "10000"))

if not TELEGRAM_TOKEN:
    logger.critical("Не найден токен бота. Задайте TELEGRAM_TOKEN (или TELEGRAM_BOT_TOKEN) в Environment Variables на Render.")
    sys.exit(1)
if not GEMINI_API_KEY:
    logger.critical("Не найден GEMINI_API_KEY. Задайте его в Environment Variables на Render.")
    sys.exit(1)
if not MONGO_URI:
    logger.warning("MONGO_URI не задан — лимиты и PRO-подписки будут храниться только в памяти процесса "
                    "и обнулятся при перезапуске/засыпании инстанса на Render. Рекомендуется подключить MongoDB.")

# ---------------------------------------------------------------------------------
# БРЕНДИНГ И МОДЕЛИ
# ---------------------------------------------------------------------------------
BOT_BRAND_NAME = "NeuroCore Omega | AI"
FREE_MODEL_BRAND = "NeuroCore Omega 2.4 Free"
PRO_MODEL_BRAND = "NeuroCore Omega 3.1 Pro"
VISION_MODEL_BRAND = "NeuroCore Vision 1.5"

# Реальные ID моделей Gemini, которые стоят за брендами (актуальны на момент написания).
TEXT_MODEL_BY_TIER = {
    "free": "gemini-3.5-flash",
    "pro": "gemini-3.7-flash",
}
IMAGE_MODEL_PRIMARY = "gemini-3.1-flash-image"       # основная модель генерации картинок
IMAGE_MODEL_FALLBACK = "gemini-3.1-flash-lite-image"  # облегчённый резерв при сбое основной

# ---------------------------------------------------------------------------------
# ЛИМИТЫ (окно — 24 часа, обнуляется автоматически)
# ---------------------------------------------------------------------------------
WINDOW_SECONDS = 24 * 60 * 60
LIMITS = {
    "free": {"messages": 30, "photos": 3, "images": 1},
    "pro": {"messages": 100, "photos": 30, "images": 10},
}

# ---------------------------------------------------------------------------------
# ТАРИФНЫЕ ПЛАНЫ PRO (Telegram Stars, валюта XTR)
# ---------------------------------------------------------------------------------
# Формат: (месяцы, цена в звёздах, подпись)
# Цены заданы со скидкой за более длинный срок — при необходимости легко поменять.
PRO_PLANS: List[Tuple[int, int, str]] = [
    (1, 25, "1 месяц — 25 ⭐"),
    (3, 65, "3 месяца — 65 ⭐"),
    (6, 120, "6 месяцев — 120 ⭐"),
    (12, 220, "12 месяцев — 220 ⭐"),
    (24, 400, "24 месяца — 400 ⭐"),
]

# ---------------------------------------------------------------------------------
# СИСТЕМНЫЙ ПРОМПТ
# ---------------------------------------------------------------------------------
def build_system_prompt(tier: str) -> str:
    """Собирает системный промпт с учётом текущего тарифа пользователя."""
    model_brand = PRO_MODEL_BRAND if tier == "pro" else FREE_MODEL_BRAND
    return f"""
Тебя зовут {BOT_BRAND_NAME}, ты работаешь в режиме "{model_brand}".
Если тебя спрашивают, какая ты модель или кто тебя создал — называй только своё имя
и режим ({model_brand}), никогда не упоминай Gemini, Google или другие компании-разработчики.

СТИЛЬ ОБЩЕНИЯ:
- Обращайся на «Вы», уважительно, но тепло и живо, без канцелярита.
- Если пользователь сам переходит на «ты» — естественно подстраивайся и общайся на «ты».
- Проявляй эмпатию, если пользователь делится проблемой. Не выдумывай факты, если не уверена(-ен).
- Используй эмодзи умеренно — один-два на сообщение, только там, где это уместно.

ЭКСПЕРТИЗА В КОДЕ:
- Пишешь идиоматичный, рабочий код на любом языке программирования по запросу.
- Код оформляй в блоках ```язык ... ``` с содержательными комментариями.
- Указывай на подводные камни (эффективность, безопасность, edge-cases), если они есть.

Отвечай по существу, используй Markdown-разметку Telegram (жирный текст, списки, код) там, где уместно.
Ты умеешь анализировать присланные изображения: описывать содержимое, читать текст, разбирать код на скриншотах.
""".strip()


# ---------------------------------------------------------------------------------
# ИНИЦИАЛИЗАЦИЯ GOOGLE GENAI
# ---------------------------------------------------------------------------------
try:
    genai_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info("Клиент Google GenAI инициализирован.")
except Exception as exc:
    logger.critical("Не удалось инициализировать Google GenAI: %s", exc)
    sys.exit(1)


# ---------------------------------------------------------------------------------
# ХРАНИЛИЩЕ: MongoDB (если есть MONGO_URI) либо память процесса (fallback)
# ---------------------------------------------------------------------------------
class Storage:
    """
    Унифицированное хранилище лимитов и PRO-подписки пользователя.
    Работает поверх MongoDB (через motor) либо, если MONGO_URI не задан,
    поверх обычного словаря в памяти — так бота можно тестировать локально
    без базы данных, но в проде MongoDB обязательна для сохранности данных
    между перезапусками/засыпаниями инстанса на Render.
    """

    def __init__(self, mongo_uri: Optional[str]):
        self._memory: Dict[int, Dict[str, Any]] = {}
        self._collection = None
        if mongo_uri:
            try:
                from motor.motor_asyncio import AsyncIOMotorClient
                client = AsyncIOMotorClient(mongo_uri)
                self._collection = client["neurocore_omega"]["users"]
                logger.info("Подключение к MongoDB установлено, лимиты будут сохраняться постоянно.")
            except Exception as exc:
                logger.error("Не удалось подключиться к MongoDB (%s), переключаюсь на память процесса.", exc)
                self._collection = None

    @staticmethod
    def _default_doc(user_id: int) -> Dict[str, Any]:
        return {
            "user_id": user_id,
            "is_pro": False,
            "pro_expires_at": None,      # unix-время окончания подписки
            "window_start": time.time(),
            "messages_used": 0,
            "photos_used": 0,
            "images_used": 0,
        }

    async def get_user(self, user_id: int) -> Dict[str, Any]:
        if self._collection is not None:
            doc = await self._collection.find_one({"user_id": user_id})
            if doc is None:
                doc = self._default_doc(user_id)
                await self._collection.insert_one(dict(doc))
            return doc
        return self._memory.setdefault(user_id, self._default_doc(user_id))

    async def update_user(self, user_id: int, updates: Dict[str, Any]) -> None:
        if self._collection is not None:
            await self._collection.update_one({"user_id": user_id}, {"$set": updates}, upsert=True)
        else:
            self._memory.setdefault(user_id, self._default_doc(user_id)).update(updates)


storage = Storage(MONGO_URI)


# ---------------------------------------------------------------------------------
# БИЗНЕС-ЛОГИКА: ТАРИФ, ЛИМИТЫ, ОКНО 24 ЧАСА
# ---------------------------------------------------------------------------------
def effective_tier(user: Dict[str, Any]) -> str:
    """Возвращает 'pro', если подписка активна и не истекла, иначе 'free'."""
    if user.get("is_pro") and user.get("pro_expires_at") and user["pro_expires_at"] > time.time():
        return "pro"
    return "free"


async def ensure_fresh_window(user_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    """Если с начала окна прошло 24 часа — обнуляет счётчики использования."""
    now = time.time()
    if now - user.get("window_start", 0) >= WINDOW_SECONDS:
        updates = {"window_start": now, "messages_used": 0, "photos_used": 0, "images_used": 0}
        user.update(updates)
        await storage.update_user(user_id, updates)
    return user


def time_left_str(user: Dict[str, Any]) -> str:
    """Человекочитаемое время до сброса лимита."""
    remaining = max(0.0, WINDOW_SECONDS - (time.time() - user.get("window_start", time.time())))
    hours, minutes = int(remaining // 3600), int((remaining % 3600) // 60)
    return f"{hours} ч {minutes} мин"


async def try_consume(user_id: int, kind: str) -> Tuple[bool, Dict[str, Any], str]:
    """
    Пытается "потратить" одну единицу лимита (kind: messages/photos/images).
    Возвращает (разрешено?, актуальный документ пользователя, текущий тариф).
    Если подписка PRO истекла — автоматически понижает пользователя до Free.
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
    """
    Активирует/продлевает PRO-подписку на заданное число месяцев.
    Если подписка уже активна — продлевает от текущей даты окончания,
    иначе — от текущего момента. Возвращает unix-время новой даты окончания.
    """
    user = await storage.get_user(user_id)
    now = time.time()
    base = user["pro_expires_at"] if (user.get("is_pro") and user.get("pro_expires_at", 0) > now) else now
    new_expiry = base + months * 30 * 24 * 60 * 60  # месяц считаем как 30 суток
    await storage.update_user(user_id, {"is_pro": True, "pro_expires_at": new_expiry})
    return new_expiry


# ---------------------------------------------------------------------------------
# КЛАВИАТУРЫ
# ---------------------------------------------------------------------------------
BTN_PRO = "⭐ PRO Подписка"
BTN_ART = "🎨 Сгенерировать арт"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_PRO), KeyboardButton(text=BTN_ART)]],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Напишите сообщение или выберите действие…",
)


def build_pro_plans_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-клавиатура выбора срока PRO-подписки (данные берутся из PRO_PLANS)."""
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"buy_pro:{months}")]
        for months, _stars, label in PRO_PLANS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------------
# ПАМЯТЬ ДИАЛОГА (история чата — только в оперативной памяти, без Mongo)
# ---------------------------------------------------------------------------------
MAX_HISTORY_MESSAGES = 20
HISTORY: Dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))
AWAITING_ART: Dict[int, bool] = defaultdict(bool)


# ---------------------------------------------------------------------------------
# ВЗАИМОДЕЙСТВИЕ С GEMINI
# ---------------------------------------------------------------------------------
async def download_photo_bytes(bot: Bot, file_id: str) -> Optional[bytes]:
    """Скачивает фото из Telegram по file_id. Возвращает None при ошибке."""
    try:
        file_info = await bot.get_file(file_id)
        stream = await bot.download_file(file_info.file_path)
        return stream.read()
    except (TelegramAPIError, Exception) as exc:  # noqa: BLE001
        logger.error("Ошибка скачивания фото %s: %s", file_id, exc)
        return None


async def ask_gemini(
    user_id: int, tier: str, user_text: str,
    image_bytes: Optional[bytes] = None, image_mime_type: str = "image/jpeg",
) -> str:
    """
    Отправляет сообщение (и опционально фото) в Gemini с учётом истории диалога
    и системного промпта, соответствующего текущему тарифу пользователя.
    """
    history = HISTORY[user_id]

    parts: List[Any] = []
    if image_bytes:
        parts.append(genai_types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type))
    text_for_model = user_text.strip() or "Опиши, что изображено на этой картинке."
    parts.append(genai_types.Part.from_text(text=text_for_model))

    history.append({"role": "user", "parts": parts})
    contents = list(history)

    config = genai_types.GenerateContentConfig(
        system_instruction=build_system_prompt(tier),
        temperature=0.8,
        max_output_tokens=4096,
    )

    try:
        response = await asyncio.to_thread(
            genai_client.models.generate_content,
            model=TEXT_MODEL_BY_TIER[tier],
            contents=contents,
            config=config,
        )
        answer = (response.text or "").strip() or "Не получилось сформировать ответ, попробуйте переформулировать запрос."
        history.append({"role": "model", "parts": [genai_types.Part.from_text(text=answer)]})
        return answer
    except GenAIAPIError as exc:
        logger.error("Ошибка Gemini API: %s", exc)
        return "Сейчас технические неполадки при обращении к модели. Попробуйте, пожалуйста, ещё раз через минуту."
    except Exception as exc:  # noqa: BLE001
        logger.error("Непредвиденная ошибка ask_gemini: %s\n%s", exc, traceback.format_exc())
        return "Произошла непредвиденная ошибка. Попробуйте, пожалуйста, чуть позже."


async def _generate_image(prompt: str, model_name: str) -> Optional[bytes]:
    """Один вызов модели генерации изображений. Возвращает байты картинки либо None."""
    response = await asyncio.to_thread(
        genai_client.models.generate_content,
        model=model_name,
        contents=[genai_types.Part.from_text(text=prompt)],
    )
    if not response.candidates:
        return None
    for part in response.candidates[0].content.parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data and inline_data.data:
            return inline_data.data
    return None


async def generate_art_with_fallback(prompt: str) -> Optional[bytes]:
    """
    Генерирует изображение основной моделью (NeuroCore Vision 1.5),
    при любом сбое — автоматически переключается на облегчённую резервную модель.
    """
    try:
        logger.info("Генерация изображения основной моделью '%s'…", IMAGE_MODEL_PRIMARY)
        image_bytes = await _generate_image(prompt, IMAGE_MODEL_PRIMARY)
        if image_bytes:
            return image_bytes
        logger.warning("Основная модель вернула пустой результат, пробуем резервную.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Основная модель генерации недоступна (%s), пробуем резервную.", exc)

    try:
        logger.info("Генерация изображения резервной моделью '%s'…", IMAGE_MODEL_FALLBACK)
        return await _generate_image(prompt, IMAGE_MODEL_FALLBACK)
    except Exception as exc:  # noqa: BLE001
        logger.error("Резервная модель тоже не сработала: %s", exc)
        return None


# ---------------------------------------------------------------------------------
# ХЭНДЛЕРЫ AIOGRAM
# ---------------------------------------------------------------------------------
router = Router(name="main_router")


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    user_id = message.from_user.id
    HISTORY.pop(user_id, None)
    AWAITING_ART[user_id] = False
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"Здравствуйте, {name}! Я — {BOT_BRAND_NAME}. 🙂\n\n"
        "Помогу разобраться в вопросе, написать и отладить код на любом языке, "
        "разобрать изображение, а по запросу — сгенерирую картинку.\n\n"
        "Пишите сообщение, и мы начнём. Если захотите — можем перейти на «ты» в любой момент.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("reset"))
async def handle_reset(message: Message) -> None:
    HISTORY.pop(message.from_user.id, None)
    await message.answer("История диалога очищена.", reply_markup=MAIN_KEYBOARD)


@router.message(Command("status"))
async def handle_status(message: Message) -> None:
    user_id = message.from_user.id
    user = await storage.get_user(user_id)
    user = await ensure_fresh_window(user_id, user)
    tier = effective_tier(user)
    lim = LIMITS[tier]
    brand = PRO_MODEL_BRAND if tier == "pro" else FREE_MODEL_BRAND

    text = (
        f"📊 *Ваш тариф:* {brand}\n\n"
        f"Сообщения: {user.get('messages_used', 0)}/{lim['messages']}\n"
        f"Фото: {user.get('photos_used', 0)}/{lim['photos']}\n"
        f"Генерации арта: {user.get('images_used', 0)}/{lim['images']}\n\n"
        f"Сброс лимита через: {time_left_str(user)}"
    )
    if tier == "pro" and user.get("pro_expires_at"):
        days_left = int((user["pro_expires_at"] - time.time()) // 86400)
        text += f"\nPRO активен ещё {days_left} дн."
    await message.answer(text, reply_markup=MAIN_KEYBOARD)


@router.message(F.text == BTN_PRO)
async def handle_pro_button(message: Message) -> None:
    await message.answer(
        "⭐ *PRO Подписка* — 100 сообщений, 30 фото и 10 генераций арта в сутки.\n\n"
        "Выберите срок подписки:",
        reply_markup=build_pro_plans_keyboard(),
    )


@router.callback_query(F.data.startswith("buy_pro:"))
async def handle_buy_pro_callback(callback: CallbackQuery, bot: Bot) -> None:
    """Пользователь выбрал срок подписки — выставляем инвойс в Telegram Stars."""
    months = int(callback.data.split(":")[1])
    plan = next((p for p in PRO_PLANS if p[0] == months), None)
    if plan is None:
        await callback.answer("Такой план не найден.", show_alert=True)
        return

    _, stars, label = plan
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=f"{PRO_MODEL_BRAND} на {months} мес.",
        description=f"Подписка PRO ({label.split('—')[0].strip()}) на {BOT_BRAND_NAME}",
        payload=f"pro_{months}_{callback.from_user.id}",
        provider_token="",       # для оплаты звёздами провайдер-токен не нужен
        currency="XTR",
        prices=[LabeledPrice(label=f"PRO на {months} мес.", amount=stars)],
    )
    await callback.answer()


@router.pre_checkout_query()
async def handle_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    """Подтверждаем готовность принять оплату (обязательный шаг Bot API)."""
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def handle_successful_payment(message: Message) -> None:
    """Обрабатывает подтверждённый платёж, активирует PRO-подписку."""
    payload = message.successful_payment.invoice_payload
    try:
        _, months_str, user_id_str = payload.split("_")
        months, user_id = int(months_str), int(user_id_str)
    except (ValueError, AttributeError):
        logger.error("Некорректный payload платежа: %s", payload)
        return

    new_expiry = await activate_pro(user_id, months)
    expiry_date = time.strftime("%d.%m.%Y", time.localtime(new_expiry))
    await message.answer(
        f"✅ Оплата получена! {PRO_MODEL_BRAND} активирован до {expiry_date}.\n"
        "Спасибо, что пользуетесь нашим ботом! 🙌",
        reply_markup=MAIN_KEYBOARD,
    )
    logger.info("Пользователь %s оформил PRO на %d мес. (до %s)", user_id, months, expiry_date)


@router.message(F.text == BTN_ART)
async def handle_art_button(message: Message) -> None:
    user_id = message.from_user.id
    allowed, user, tier = await try_consume(user_id, "images")
    if not allowed:
        await message.answer(
            f"Лимит генераций арта на тарифе {PRO_MODEL_BRAND if tier == 'pro' else FREE_MODEL_BRAND} исчерпан.\n"
            f"Обновится через {time_left_str(user)}. "
            + ("" if tier == "pro" else "Оформите PRO для увеличения лимита."),
            reply_markup=MAIN_KEYBOARD,
        )
        return
    AWAITING_ART[user_id] = True
    await message.answer(
        "Опишите, пожалуйста, что бы Вы хотели увидеть на картинке — чем подробнее "
        "(стиль, цвета, композиция), тем точнее результат.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    allowed, user, tier = await try_consume(user_id, "photos")
    if not allowed:
        await message.answer(
            f"Лимит на фото исчерпан. Обновится через {time_left_str(user)}. "
            + ("" if tier == "pro" else "Оформите PRO для увеличения лимита."),
            reply_markup=MAIN_KEYBOARD,
        )
        return

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    image_bytes = await download_photo_bytes(bot, message.photo[-1].file_id)
    if image_bytes is None:
        await message.answer("Не получилось загрузить изображение. Попробуйте ещё раз.", reply_markup=MAIN_KEYBOARD)
        return

    answer = await ask_gemini(user_id, tier, message.caption or "", image_bytes)
    await message.answer(answer, reply_markup=MAIN_KEYBOARD)


@router.message(F.text)
async def handle_text(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    text = message.text or ""

    # Если пользователь ранее нажал "Сгенерировать арт" — этот текст является промптом.
    if AWAITING_ART.get(user_id):
        AWAITING_ART[user_id] = False
        await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
        image_bytes = await generate_art_with_fallback(text)
        if image_bytes is None:
            await message.answer(
                "Не удалось сгенерировать изображение (сбой сервиса). Попробуйте ещё раз позже.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        await message.answer_photo(
            photo=BufferedInputFile(image_bytes, filename="art.png"),
            caption=f'Готово! По описанию: "{text}"',
            reply_markup=MAIN_KEYBOARD,
        )
        return

    allowed, user, tier = await try_consume(user_id, "messages")
    if not allowed:
        await message.answer(
            f"Дневной лимит сообщений исчерпан. Обновится через {time_left_str(user)}. "
            + ("" if tier == "pro" else "Оформите PRO для увеличения лимита."),
            reply_markup=MAIN_KEYBOARD,
        )
        return

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    answer = await ask_gemini(user_id, tier, text)
    await message.answer(answer, reply_markup=MAIN_KEYBOARD)


@router.message()
async def handle_unsupported(message: Message) -> None:
    await message.answer("Пока умею работать с текстом и изображениями.", reply_markup=MAIN_KEYBOARD)


# ---------------------------------------------------------------------------------
# HEALTH-CHECK СЕРВЕР (обязателен, чтобы Render не считал деплой упавшим)
# ---------------------------------------------------------------------------------
async def handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "bot": BOT_BRAND_NAME, "time": time.time()})


async def run_web_server() -> None:
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info("Health-check сервер запущен на 0.0.0.0:%d", PORT)
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await runner.cleanup()
        raise


# ---------------------------------------------------------------------------------
# ПОЛЛИНГ БОТА С АВТОПЕРЕЗАПУСКОМ ПРИ СБОЯХ
# ---------------------------------------------------------------------------------
async def run_bot_polling() -> None:
    bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    delay = 5
    while True:
        try:
            logger.info("Запуск long-polling %s…", BOT_BRAND_NAME)
            await dispatcher.start_polling(
                bot,
                drop_pending_updates=True,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
            break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Поллинг упал: %s\n%s", exc, traceback.format_exc())
            logger.info("Повтор через %d сек…", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    await bot.session.close()


# ---------------------------------------------------------------------------------
# ТОЧКА ВХОДА
# ---------------------------------------------------------------------------------
async def main() -> None:
    logger.info("=" * 60)
    logger.info("Запуск %s | Free: %s | Pro: %s | Vision: %s",
                BOT_BRAND_NAME, TEXT_MODEL_BY_TIER["free"], TEXT_MODEL_BY_TIER["pro"], IMAGE_MODEL_PRIMARY)
    logger.info("=" * 60)
    await asyncio.gather(run_web_server(), run_bot_polling())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлено.")
    except Exception as exc:  # noqa: BLE001
        logger.critical("Фатальная ошибка: %s\n%s", exc, traceback.format_exc())
        sys.exit(1)

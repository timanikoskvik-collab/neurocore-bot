# -*- coding: utf-8 -*-
"""
mechanics.py — вся Telegram-механика: клавиатуры, команда /start, реакция на
кнопки, приём фото/текста, оплата PRO через Telegram Stars.

ВАЖНО: сама работа с Gemini (генерация текста и картинок) физически находится
в NeoroCore.AI.py — это "основа" по вашей просьбе. Но файл с точкой в имени
(NeoroCore.AI.py) нельзя импортировать обычной командой `import` в Python —
конструкция `import NeoroCore.AI` означает "пакет NeoroCore, модуль AI",
а не наш файл.

Поэтому связь сделана в обратную сторону: NeoroCore.AI.py при старте вызывает
mechanics.init(...) и передаёт сюда свои функции (ask_gemini, генерацию арта,
скачивание фото) — это стандартный приём "внедрения зависимостей". Сам файл
mechanics.py при этом ничего не импортирует из NeoroCore.AI.py напрямую.
"""

import time
import logging
from typing import Dict, Callable, Optional

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    LabeledPrice, PreCheckoutQuery,
)

import database

logger = logging.getLogger("neurocore_omega.mechanics")

router = Router(name="main_router")

# ---------------------------------------------------------------------------
# ФУНКЦИИ ИИ, ПОЛУЧЕННЫЕ ОТ NeoroCore.AI.py ЧЕРЕЗ init()
# ---------------------------------------------------------------------------
_ask_gemini: Optional[Callable] = None
_generate_art_with_fallback: Optional[Callable] = None
_download_photo_bytes: Optional[Callable] = None


def init(ask_gemini: Callable, generate_art_with_fallback: Callable, download_photo_bytes: Callable) -> None:
    """
    Вызывается один раз из NeoroCore.AI.py перед запуском поллинга — передаёт
    сюда реальные функции работы с Gemini. До этого вызова хэндлеры ниже
    не смогут ничего сгенерировать (что и логично — не полагаемся на порядок
    импортов, а явно проверяем готовность в main()).
    """
    global _ask_gemini, _generate_art_with_fallback, _download_photo_bytes
    _ask_gemini = ask_gemini
    _generate_art_with_fallback = generate_art_with_fallback
    _download_photo_bytes = download_photo_bytes
    logger.info("ИИ-функции успешно подключены к механике бота.")


# ---------------------------------------------------------------------------
# КЛАВИАТУРЫ
# ---------------------------------------------------------------------------
BTN_PRO = "⭐ PRO Подписка"
BTN_ART = "🎨 Сгенерировать арт"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_PRO), KeyboardButton(text=BTN_ART)]],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Напишите сообщение или выберите действие…",
)


def build_pro_plans_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"buy_pro:{months}")]
        for months, _stars, label in database.PRO_PLANS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Состояние "ожидаю промпт для арта" — интерфейсный флаг, не бизнес-данные,
# поэтому живёт в памяти процесса (не критично потерять при рестарте).
AWAITING_ART: Dict[int, bool] = {}


# ---------------------------------------------------------------------------
# БАЗОВЫЕ КОМАНДЫ (старт, сброс памяти, статус лимитов)
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    user_id = message.from_user.id
    AWAITING_ART[user_id] = False
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"Здравствуйте, {name}! Я — {database.BOT_BRAND_NAME}. 🙂\n\n"
        "Помогу разобраться в вопросе, написать и отладить код, разобрать "
        "изображение (например, страницу учебника или скриншот), а по запросу — "
        "сгенерирую картинку. Я запоминаю контекст диалога даже между сессиями.\n\n"
        "Пишите сообщение, и мы начнём.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("reset"))
async def handle_reset(message: Message) -> None:
    await database.clear_history(message.from_user.id)
    await message.answer("Память диалога очищена.", reply_markup=MAIN_KEYBOARD)


@router.message(Command("status"))
async def handle_status(message: Message) -> None:
    user_id = message.from_user.id
    user = await database.storage.get_user(user_id)
    user = await database.ensure_fresh_window(user_id, user)
    tier = database.effective_tier(user)
    lim = database.LIMITS[tier]
    brand = database.PRO_MODEL_BRAND if tier == "pro" else database.FREE_MODEL_BRAND

    text = (
        f"📊 *Ваш тариф:* {brand}\n\n"
        f"Сообщения: {user.get('messages_used', 0)}/{lim['messages']}\n"
        f"Фото: {user.get('photos_used', 0)}/{lim['photos']}\n"
        f"Генерации арта: {user.get('images_used', 0)}/{lim['images']}\n\n"
        f"Сброс лимита через: {database.time_left_str(user)}"
    )
    if tier == "pro" and user.get("pro_expires_at"):
        days_left = int((user["pro_expires_at"] - time.time()) // 86400)
        text += f"\nPRO активен ещё {days_left} дн."
    await message.answer(text, reply_markup=MAIN_KEYBOARD)


# ---------------------------------------------------------------------------
# PRO-ПОДПИСКА (Telegram Stars)
# ---------------------------------------------------------------------------
@router.message(F.text == BTN_PRO)
async def handle_pro_button(message: Message) -> None:
    await message.answer(
        "⭐ *PRO Подписка* — 100 сообщений, 30 фото и 10 генераций арта в сутки.\n\n"
        "Выберите срок подписки:",
        reply_markup=build_pro_plans_keyboard(),
    )


@router.callback_query(F.data.startswith("buy_pro:"))
async def handle_buy_pro_callback(callback: CallbackQuery, bot: Bot) -> None:
    months = int(callback.data.split(":")[1])
    plan = next((p for p in database.PRO_PLANS if p[0] == months), None)
    if plan is None:
        await callback.answer("Такой план не найден.", show_alert=True)
        return

    _, stars, label = plan
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=f"{database.PRO_MODEL_BRAND} на {months} мес.",
        description=f"Подписка PRO ({label.split('—')[0].strip()}) на {database.BOT_BRAND_NAME}",
        payload=f"pro_{months}_{callback.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"PRO на {months} мес.", amount=stars)],
    )
    await callback.answer()


@router.pre_checkout_query()
async def handle_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def handle_successful_payment(message: Message) -> None:
    payload = message.successful_payment.invoice_payload
    try:
        _, months_str, user_id_str = payload.split("_")
        months, user_id = int(months_str), int(user_id_str)
    except (ValueError, AttributeError):
        logger.error("Некорректный payload платежа: %s", payload)
        return

    new_expiry = await database.activate_pro(user_id, months)
    expiry_date = time.strftime("%d.%m.%Y", time.localtime(new_expiry))
    await message.answer(
        f"✅ Оплата получена! {database.PRO_MODEL_BRAND} активирован до {expiry_date}.\n"
        "Спасибо, что пользуетесь нашим ботом! 🙌",
        reply_markup=MAIN_KEYBOARD,
    )
    logger.info("Пользователь %s оформил PRO на %d мес. (до %s)", user_id, months, expiry_date)


# ---------------------------------------------------------------------------
# ГЕНЕРАЦИЯ АРТА
# ---------------------------------------------------------------------------
@router.message(F.text == BTN_ART)
async def handle_art_button(message: Message) -> None:
    user_id = message.from_user.id
    allowed, user, tier = await database.try_consume(user_id, "images")
    if not allowed:
        brand = database.PRO_MODEL_BRAND if tier == "pro" else database.FREE_MODEL_BRAND
        await message.answer(
            f"Лимит генераций арта на тарифе {brand} исчерпан.\n"
            f"Обновится через {database.time_left_str(user)}. "
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


# ---------------------------------------------------------------------------
# ОБРАБОТКА ФОТО (мультимодальный ввод — читает изображение и отвечает по нему)
# ---------------------------------------------------------------------------
@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    allowed, user, tier = await database.try_consume(user_id, "photos")
    if not allowed:
        await message.answer(
            f"Лимит на фото исчерпан. Обновится через {database.time_left_str(user)}. "
            + ("" if tier == "pro" else "Оформите PRO для увеличения лимита."),
            reply_markup=MAIN_KEYBOARD,
        )
        return

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    image_bytes = await _download_photo_bytes(bot, message.photo[-1].file_id)
    if image_bytes is None:
        await message.answer("Не получилось загрузить изображение. Попробуйте ещё раз.", reply_markup=MAIN_KEYBOARD)
        return

    answer = await _ask_gemini(user_id, tier, message.caption or "", image_bytes)
    await message.answer(answer, reply_markup=MAIN_KEYBOARD)


# ---------------------------------------------------------------------------
# ОБРАБОТКА ТЕКСТА
# ---------------------------------------------------------------------------
@router.message(F.text)
async def handle_text(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    text = message.text or ""

    if AWAITING_ART.get(user_id):
        AWAITING_ART[user_id] = False
        await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
        image_bytes, reason = await _generate_art_with_fallback(text)
        if image_bytes is None:
            if reason == "safety":
                text_reply = (
                    "Этот запрос не проходит по правилам безопасности сервиса генерации "
                    "изображений (например, военная тематика, оружие, насилие). "
                    "Попробуйте описать картинку иначе."
                )
            else:
                text_reply = "Не удалось сгенерировать изображение (техническая неполадка). Попробуйте ещё раз позже."
            await message.answer(text_reply, reply_markup=MAIN_KEYBOARD)
            return
        await message.answer_photo(
            photo=BufferedInputFile(image_bytes, filename="art.png"),
            caption=f'Готово! По описанию: "{text}"',
            reply_markup=MAIN_KEYBOARD,
        )
        return

    allowed, user, tier = await database.try_consume(user_id, "messages")
    if not allowed:
        await message.answer(
            f"Дневной лимит сообщений исчерпан. Обновится через {database.time_left_str(user)}. "
            + ("" if tier == "pro" else "Оформите PRO для увеличения лимита."),
            reply_markup=MAIN_KEYBOARD,
        )
        return

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    answer = await _ask_gemini(user_id, tier, text)
    await message.answer(answer, reply_markup=MAIN_KEYBOARD)


@router.message()
async def handle_unsupported(message: Message) -> None:
    await message.answer("Пока умею работать с текстом и изображениями.", reply_markup=MAIN_KEYBOARD)

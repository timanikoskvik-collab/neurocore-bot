#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeoroCore.AI.py — ОСНОВНОЙ файл NeuroCore Omega | AI.

Здесь физически находится вся работа с искусственным интеллектом:
  - обычный текстовый диалог (Gemini, с постоянной памятью из database.py),
  - анализ присланных фото (мультимодальный ввод),
  - генерация изображений с автопереключением на резервную модель.

А также — запуск процесса: health-check сервер для Render и long-polling
Telegram-бота. Кнопки, команды, оплата — в mechanics.py; настройки, лимиты
и хранилище — в database.py.

Запускается командой:  python NeoroCore.AI.py
"""

import sys
import time
import asyncio
import logging
import traceback
from typing import Optional, Any, Dict, List, Tuple

from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from google import genai
from google.genai import types as genai_types
from google.genai.errors import APIError as GenAIAPIError

import database  # настройки, лимиты, PRO-подписка, память диалога

# ---------------------------------------------------------------------------
# ЛОГИРОВАНИЕ
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logger = logging.getLogger("neurocore_omega.main")

# ---------------------------------------------------------------------------
# КЛИЕНТ GOOGLE GENAI
# ---------------------------------------------------------------------------
genai_client = genai.Client(api_key=database.GEMINI_API_KEY)


def build_system_prompt(tier: str) -> str:
    """Системный промпт с учётом текущего тарифа пользователя."""
    model_brand = database.PRO_MODEL_BRAND if tier == "pro" else database.FREE_MODEL_BRAND
    return f"""
Тебя зовут {database.BOT_BRAND_NAME}, ты работаешь в режиме "{model_brand}".
Если тебя спрашивают, какая ты модель или кто тебя создал — называй только своё имя
и режим ({model_brand}), никогда не упоминай Gemini, Google или другие компании-разработчики.

СТИЛЬ ОБЩЕНИЯ:
- Обращайся на «Вы», уважительно, но тепло и живо, без канцелярита.
- Если пользователь сам переходит на «ты» — естественно подстраивайся и общайся на «ты».
- Проявляй эмпатию, если пользователь делится проблемой. Не выдумывай факты, если не уверена(-ен).
- Используй эмодзи умеренно — один-два на сообщение, только там, где это уместно.

ЭКСПЕРТИЗА В КОДЕ И УЧЁБЕ:
- Пишешь идиоматичный, рабочий код на любом языке программирования по запросу.
- Код оформляй в блоках ```язык ... ``` с содержательными комментариями.
- Помогая с домашними заданиями, объясняй ход решения по шагам, а не только конечный ответ.

Отвечай по существу, используй Markdown-разметку Telegram там, где уместно.
Ты умеешь анализировать присланные изображения: описывать содержимое, читать текст,
разбирать задачи и примеры на фотографиях учебников, тетрадей и скриншотов.
Если на фото несколько заданий — по умолчанию разбери их все по порядку, если
пользователь явно не попросил конкретный номер.
""".strip()


# ---------------------------------------------------------------------------
# СКАЧИВАНИЕ ФОТО
# ---------------------------------------------------------------------------
async def download_photo_bytes(bot: Bot, file_id: str) -> Optional[bytes]:
    """Скачивает фото из Telegram по file_id (берётся вариант максимального качества)."""
    try:
        file_info = await bot.get_file(file_id)
        stream = await bot.download_file(file_info.file_path)
        return stream.read()
    except (TelegramAPIError, Exception) as exc:  # noqa: BLE001
        logger.error("Ошибка скачивания фото %s: %s", file_id, exc)
        return None


# ---------------------------------------------------------------------------
# ТЕКСТ + АНАЛИЗ ФОТО (основной ИИ-диалог)
# ---------------------------------------------------------------------------
async def ask_gemini(
    user_id: int, tier: str, user_text: str,
    image_bytes: Optional[bytes] = None, image_mime_type: str = "image/jpeg",
) -> str:
    """
    Отправляет сообщение (и опционально фото) в Gemini с учётом ПОСТОЯННОЙ истории
    диалога (из database.py), системного промпта под тариф и ускоренного уровня
    "размышления" (иначе модели Gemini 3.x отвечают заметно медленнее).
    """
    text_for_model = user_text.strip() or "Опиши, что изображено на этой картинке."

    history = await database.get_history(user_id)
    contents: List[Any] = [
        {"role": entry["role"], "parts": [genai_types.Part.from_text(text=entry["text"])]}
        for entry in history
    ]

    current_parts: List[Any] = []
    if image_bytes:
        current_parts.append(genai_types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type))
    current_parts.append(genai_types.Part.from_text(text=text_for_model))
    contents.append({"role": "user", "parts": current_parts})

    gen_config = genai_types.GenerateContentConfig(
        system_instruction=build_system_prompt(tier),
        temperature=0.8,
        max_output_tokens=4096,
        thinking_config=genai_types.ThinkingConfig(thinking_level=database.THINKING_LEVEL_BY_TIER[tier]),
    )

    try:
        response = await asyncio.to_thread(
            genai_client.models.generate_content,
            model=database.TEXT_MODEL_BY_TIER[tier],
            contents=contents,
            config=gen_config,
        )
        answer = (response.text or "").strip() or "Не получилось сформировать ответ, попробуйте переформулировать запрос."
        await database.append_history(user_id, text_for_model, answer)
        return answer

    except GenAIAPIError as exc:
        logger.error("Ошибка Gemini API: %s", exc)
        return "Сейчас технические неполадки при обращении к модели. Попробуйте, пожалуйста, ещё раз через минуту."
    except Exception as exc:  # noqa: BLE001
        logger.error("Непредвиденная ошибка ask_gemini: %s\n%s", exc, traceback.format_exc())
        return "Произошла непредвиденная ошибка. Попробуйте, пожалуйста, чуть позже."


# ---------------------------------------------------------------------------
# ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ (с fallback на резервную модель)
# ---------------------------------------------------------------------------
async def _generate_image(prompt: str, model_name: str) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Один вызов модели генерации изображений.
    Возвращает (байты_картинки_или_None, причина_отказа: "safety"/"empty"/None).
    """
    response = await asyncio.to_thread(
        genai_client.models.generate_content,
        model=model_name,
        contents=[genai_types.Part.from_text(text=prompt)],
    )

    feedback = getattr(response, "prompt_feedback", None)
    if feedback and getattr(feedback, "block_reason", None):
        logger.warning("Промпт для генерации изображения заблокирован: %s", feedback.block_reason)
        return None, "safety"

    if not response.candidates:
        return None, "empty"

    candidate = response.candidates[0]
    for part in candidate.content.parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data and inline_data.data:
            return inline_data.data, None

    finish_reason = str(getattr(candidate, "finish_reason", "") or "")
    if finish_reason and finish_reason.upper() not in ("STOP", "1", "FINISH_REASON_UNSPECIFIED"):
        logger.warning("Генерация остановлена по причине: %s", finish_reason)
        return None, "safety"

    return None, "empty"


async def generate_art_with_fallback(prompt: str) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Генерирует изображение основной моделью (NeuroCore Vision 1.5), при сбое или
    пустом результате — переключается на облегчённую резервную модель.
    Возвращает (байты_или_None, причина: "safety"/"network"/None).
    """
    try:
        logger.info("Генерация изображения основной моделью '%s'…", database.IMAGE_MODEL_PRIMARY)
        image_bytes, reason = await _generate_image(prompt, database.IMAGE_MODEL_PRIMARY)
        if image_bytes:
            return image_bytes, None
        if reason == "safety":
            logger.info("Запрос отклонён политикой безопасности, резервную модель не пробуем.")
            return None, "safety"
        logger.warning("Основная модель вернула пустой результат, пробуем резервную.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Основная модель генерации недоступна (%s), пробуем резервную.", exc)

    try:
        logger.info("Генерация изображения резервной моделью '%s'…", database.IMAGE_MODEL_FALLBACK)
        image_bytes, reason = await _generate_image(prompt, database.IMAGE_MODEL_FALLBACK)
        if image_bytes:
            return image_bytes, None
        return None, reason or "network"
    except Exception as exc:  # noqa: BLE001
        logger.error("Резервная модель тоже не сработала: %s", exc)
        return None, "network"


# ---------------------------------------------------------------------------
# ПОДКЛЮЧЕНИЕ МЕХАНИКИ (импортируем mechanics ПОСЛЕ того, как определили
# все ИИ-функции выше, и передаём их туда через init — см. mechanics.py)
# ---------------------------------------------------------------------------
import mechanics  # noqa: E402  (импорт намеренно не в начале файла — см. комментарий выше)

mechanics.init(ask_gemini, generate_art_with_fallback, download_photo_bytes)


# ---------------------------------------------------------------------------
# HEALTH-CHECK СЕРВЕР (обязателен, чтобы Render не считал деплой упавшим)
# ---------------------------------------------------------------------------
async def handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "bot": database.BOT_BRAND_NAME, "time": time.time()})


async def run_web_server() -> None:
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=database.PORT)
    await site.start()
    logger.info("Health-check сервер запущен на 0.0.0.0:%d", database.PORT)
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await runner.cleanup()
        raise


# ---------------------------------------------------------------------------
# ПОЛЛИНГ С АВТОПЕРЕЗАПУСКОМ ПРИ СБОЯХ
# ---------------------------------------------------------------------------
async def run_bot_polling() -> None:
    bot = Bot(token=database.TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dispatcher = Dispatcher()
    dispatcher.include_router(mechanics.router)

    delay = 5
    while True:
        try:
            logger.info("Запуск long-polling %s…", database.BOT_BRAND_NAME)
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


async def main() -> None:
    logger.info("=" * 60)
    logger.info("Запуск %s | Free: %s | Pro: %s | Vision: %s",
                database.BOT_BRAND_NAME, database.TEXT_MODEL_BY_TIER["free"],
                database.TEXT_MODEL_BY_TIER["pro"], database.IMAGE_MODEL_PRIMARY)
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

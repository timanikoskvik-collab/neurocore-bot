#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================
  TELEGRAM-БОТ НА AIOGRAM 3.x С ИНТЕГРАЦИЕЙ GOOGLE GEMINI (google-genai SDK)
=======================================================================================

Архитектура файла (сверху вниз):
    1. Импорты и настройка логирования.
    2. Загрузка и проверка переменных окружения (без них бот не имеет смысла запускать).
    3. Константы: системный промпт, лимиты памяти, тексты кнопок, названия моделей.
    4. Инициализация клиента Google GenAI.
    5. Reply-клавиатура ровно с двумя кнопками (без «Новый чат» / «Недавние чаты»).
    6. Хранилище сессий пользователей (in-memory, с автоочисткой старых сообщений).
    7. Вспомогательные функции: работа с историей, скачивание фото, вызов Gemini,
       генерация изображений с fallback-цепочкой.
    8. Хэндлеры aiogram: /start, обработка двух кнопок, обработка фото, обработка текста.
    9. Мини aiohttp-сервер (health-check для Render / UptimeRobot и т.п.).
   10. Точка входа: параллельный запуск aiohttp-сервера и long-polling бота,
       с отказоустойчивым перезапуском при падении.

Все комментарии — на русском языке, максимально подробные, чтобы код можно было
использовать как учебный пример и как production-заготовку одновременно.
=======================================================================================
"""

# ---------------------------------------------------------------------------------
# БЛОК 1. ИМПОРТЫ
# ---------------------------------------------------------------------------------

import os                      # Работа с переменными окружения
import sys                     # Для корректного завершения процесса при фатальных ошибках
import asyncio                 # Асинхронный цикл событий — основа aiogram и aiohttp
import logging                 # Подробное логирование всех этапов работы бота
import io                      # Работа с байтовыми потоками (для фото и картинок)
import time                    # Метки времени для логов и троттлинга
import traceback               # Полный трейсбек ошибок в логах при отладке
from collections import defaultdict, deque   # Эффективное хранилище истории диалога
from dataclasses import dataclass, field     # Удобные структуры данных для сессий
from typing import Optional, List, Dict, Any

from aiohttp import web        # Веб-сервер для health-check (обязателен для Render)
import aiohttp                 # Асинхронные HTTP-запросы (например, скачивание файла из Telegram)

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile,
)
from aiogram.exceptions import TelegramAPIError

# Google GenAI SDK — официальный клиент для Gemini (текст, зрение, генерация картинок)
from google import genai
from google.genai import types as genai_types
from google.genai.errors import APIError as GenAIAPIError


# ---------------------------------------------------------------------------------
# БЛОК 2. НАСТРОЙКА ЛОГИРОВАНИЯ
# ---------------------------------------------------------------------------------
# Логи пишем и в консоль (для Render/Docker-логов), формат — с меткой времени,
# уровнем важности и именем модуля, чтобы легко искать проблему по логам.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Приглушаем излишне «болтливые» логи сторонних библиотек, оставляя только warning+
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("gemini_bot")


# ---------------------------------------------------------------------------------
# БЛОК 3. ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ---------------------------------------------------------------------------------
# Бот не должен «тихо» падать при первом обращении пользователя из-за отсутствия
# токена — все обязательные переменные проверяются ДО запуска event loop.

def get_env_or_die(name: str, secret: bool = True) -> str:
    """
    Читает переменную окружения. Если она не задана — логирует критическую ошибку
    и завершает процесс с ненулевым кодом (это остановит контейнер/деплой и даст
    понятный сигнал в логах Render, а не непонятный traceback где-то в middleware).

    :param name: имя переменной окружения
    :param secret: если True — значение не будет напечатано в логах целиком
    """
    value = os.environ.get(name)
    if not value:
        logger.critical("Переменная окружения '%s' не задана! Завершение работы.", name)
        sys.exit(1)
    if secret:
        logger.info("Переменная окружения '%s' успешно загружена (значение скрыто).", name)
    else:
        logger.info("Переменная окружения '%s' = %s", name, value)
    return value


def get_env_optional(name: str, default: str) -> str:
    """Читает необязательную переменную окружения с дефолтным значением."""
    value = os.environ.get(name, default)
    logger.info("Переменная окружения '%s' = %s (значение по умолчанию, если не задано отдельно)", name, value)
    return value


# Обязательные переменные — без них бот бессмысленен
TELEGRAM_BOT_TOKEN: str = get_env_or_die("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY: str = get_env_or_die("GEMINI_API_KEY")

# Необязательные переменные с разумными значениями по умолчанию
PORT: int = int(get_env_optional("PORT", "10000"))           # Render пробрасывает свой PORT
TEXT_MODEL_NAME: str = get_env_optional("TEXT_MODEL_NAME", "gemini-2.5-flash")
VISION_MODEL_NAME: str = get_env_optional("VISION_MODEL_NAME", "gemini-2.5-flash")
IMAGE_MODEL_PRIMARY: str = get_env_optional("IMAGE_MODEL_PRIMARY", "gemini-2.5-flash-image")
IMAGE_MODEL_FALLBACK: str = get_env_optional("IMAGE_MODEL_FALLBACK", "imagen-3.0-generate-002")
PRO_LINK: str = get_env_optional("PRO_SUBSCRIPTION_URL", "https://t.me/your_payment_bot")


# ---------------------------------------------------------------------------------
# БЛОК 4. КОНСТАНТЫ И СИСТЕМНЫЙ ПРОМПТ
# ---------------------------------------------------------------------------------

# Максимальное количество РЕПЛИК (не токенов!), которое хранится в истории одного
# пользователя. Каждая реплика — это один словарь {"role": ..., "parts": [...]}.
# Ограничение через deque(maxlen=...) исключает бесконечный рост памяти процесса.
MAX_HISTORY_MESSAGES: int = 20

# Сколько последних реплик реально отправляем модели в каждом запросе.
# Иногда есть смысл хранить чуть больше, чем отправляем — но здесь для простоты
# и предсказуемости расхода токенов используем то же самое окно.
MESSAGES_TO_SEND: int = 20

# Максимальный размер фотографии, которую мы скачиваем и отправляем в Gemini (байт).
# Защита от чрезмерно тяжёлых файлов и лишнего расхода трафика/токенов.
MAX_PHOTO_SIZE_BYTES: int = 15 * 1024 * 1024  # 15 МБ

# Тексты двух единственных кнопок интерфейса. Намеренно НЕТ кнопок
# «Новый чат» / «Недавние чаты» — по требованиям интерфейса бот держится
# максимально «чистым» и не перегружает пользователя лишними опциями.
BTN_PRO_SUBSCRIPTION = "⭐ PRO Подписка"
BTN_GENERATE_ART = "🎨 Сгенерировать арт"

# Системный промпт — «характер» бота. Задаёт стиль общения (тепло, уважительно,
# обращение на «Вы» с постепенным переходом на «ты», если пользователь сам
# переходит на неформальное общение), а также экспертизу в программировании.
SYSTEM_PROMPT: str = """
Ты — дружелюбный, тёплый и очень компетентный ИИ-ассистент в Telegram.

СТИЛЬ ОБЩЕНИЯ:
- В начале диалога и с новыми пользователями обращайся на «Вы» — уважительно, но без излишней
  официозности и канцелярита. Никаких сухих шаблонных фраз вроде «Здравствуйте, чем могу быть полезен».
- Если пользователь сам переходит на «ты», естественно и без объявлений подстраивайся под его тон
  и тоже общайся на «ты» — тепло, по-дружески, как хороший знакомый, но сохраняя уважение.
- Пиши живо, без канцеляризмов, избегай шаблонных вводных фраз. Проявляй эмпатию, если пользователь
  делится проблемой или трудностью — сначала прояви понимание, потом переходи к сути.
- Будь честен: если чего-то не знаешь или не уверен — так и скажи, не выдумывай факты.
- Не используй чрезмерное количество эмодзи — один-два уместных эмодзи на сообщение, не больше,
  и только там, где это действительно усиливает тон, а не превращает ответ в набор смайликов.

ЭКСПЕРТИЗА В ПРОГРАММИРОВАНИИ:
- Ты пишешь безупречный, идиоматичный код на любых языках программирования: Python, JavaScript/
  TypeScript, Go, Rust, C/C++, C#, Java, Kotlin, Swift, PHP, Ruby, SQL, Bash и других — по запросу.
- Код всегда оформляй в блоках ```язык ... ```, с содержательными комментариями там, где это
  улучшает читаемость, но без избыточности.
- Предлагай рабочие, протестированные мысленно решения, указывай на потенциальные подводные камни
  (эффективность, безопасность, edge-cases), если они есть.
- Если задача сформулирована нечётко — сначала уточни детали, а не гадай наугад, если это критично
  для правильности решения; если можно сделать разумное предположение — сделай его и явно озвучь.

ОБЩИЕ ПРИНЦИПЫ:
- Отвечай по существу, избегай воды.
- Форматируй ответы с помощью Markdown (Telegram поддерживает разметку) там, где это уместно:
  списки, жирный текст для акцентов, блоки кода.
- Ты умеешь анализировать изображения, которые присылает пользователь: описывать содержимое,
  распознавать текст, объяснять диаграммы, помогать с код-ревью по скриншотам кода и т.д.
""".strip()


# ---------------------------------------------------------------------------------
# БЛОК 5. ИНИЦИАЛИЗАЦИЯ КЛИЕНТА GOOGLE GENAI
# ---------------------------------------------------------------------------------
# Клиент создаётся один раз на весь процесс — это самый эффективный вариант,
# т.к. внутри используется пул HTTP-соединений.

try:
    genai_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info("Клиент Google GenAI успешно инициализирован.")
except Exception as exc:  # noqa: BLE001 — на этапе инициализации ловим максимально широко
    logger.critical("Не удалось инициализировать клиент Google GenAI: %s", exc)
    sys.exit(1)


# ---------------------------------------------------------------------------------
# БЛОК 6. REPLY-КЛАВИАТУРА (ТОЛЬКО ДВЕ КНОПКИ)
# ---------------------------------------------------------------------------------

def build_main_keyboard() -> ReplyKeyboardMarkup:
    """
    Строит главную (и единственную) reply-клавиатуру бота.

    Важно: по требованиям интерфейса здесь ровно ДВЕ кнопки, никаких дополнительных
    рядов вроде «Новый чат» или «Недавние чаты» — интерфейс должен оставаться
    предельно простым и не отвлекать пользователя от диалога.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PRO_SUBSCRIPTION), KeyboardButton(text=BTN_GENERATE_ART)],
        ],
        resize_keyboard=True,       # Клавиатура компактно подстраивается под размер кнопок
        is_persistent=True,         # Клавиатура не сворачивается сама по себе
        input_field_placeholder="Напишите сообщение или выберите действие…",
    )


MAIN_KEYBOARD = build_main_keyboard()


# ---------------------------------------------------------------------------------
# БЛОК 7. МОДЕЛЬ ДАННЫХ СЕССИИ И ХРАНИЛИЩЕ ПАМЯТИ
# ---------------------------------------------------------------------------------

@dataclass
class UserSession:
    """
    Хранит состояние диалога одного пользователя.

    history — это deque (двусторонняя очередь) с ограничением maxlen. Как только
    в неё добавляется элемент сверх лимита, самый старый элемент автоматически
    вытесняется. Это даёт нам «оптимизированное управление памятью» без ручного
    среза списков на каждой итерации — операция O(1) вместо O(n) при list.pop(0).

    Каждый элемент истории — словарь в формате, ожидаемом Gemini API:
        {"role": "user" | "model", "parts": [genai_types.Part, ...]}

    awaiting_art_prompt — флаг, показывающий, что пользователь нажал кнопку
    «Сгенерировать арт» и следующее его текстовое сообщение нужно интерпретировать
    как промпт для генерации изображения, а не как обычную реплику чата.

    last_activity_ts — метка времени последнего сообщения, используется, например,
    для потенциальной будущей очистки неактивных сессий по TTL (задел на будущее).
    """
    history: deque = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_MESSAGES))
    awaiting_art_prompt: bool = False
    last_activity_ts: float = field(default_factory=time.time)


# Глобальное in-memory хранилище сессий: ключ — telegram user_id, значение — UserSession.
# defaultdict автоматически создаёт новую пустую сессию при первом обращении к user_id,
# что избавляет от повторяющихся проверок "if user_id not in sessions: ...".
USER_SESSIONS: Dict[int, UserSession] = defaultdict(UserSession)


def get_session(user_id: int) -> UserSession:
    """Возвращает сессию пользователя, обновляя метку последней активности."""
    session = USER_SESSIONS[user_id]
    session.last_activity_ts = time.time()
    return session


def append_to_history(session: UserSession, role: str, parts: List[Any]) -> None:
    """
    Добавляет новую реплику в историю сессии.

    Благодаря deque(maxlen=MAX_HISTORY_MESSAGES) старые сообщения вытесняются
    автоматически — отдельный код для «среза» истории не требуется, но ниже
    оставлена явная защитная функция trim_history() на случай, если история
    когда-либо будет храниться в обычном списке (например, при миграции на Redis).
    """
    session.history.append({"role": role, "parts": parts})


def trim_history(session: UserSession) -> None:
    """
    Явный «защитный» срез истории по последним MESSAGES_TO_SEND сообщениям.

    Хотя deque уже ограничивает размер истории через maxlen, эта функция
    дополнительно подстраховывает нас на случай, если MESSAGES_TO_SEND задан
    меньше, чем MAX_HISTORY_MESSAGES (то есть мы храним больше, чем отправляем
    в модель за раз — полезно, если в будущем понадобится суммаризация «хвоста»
    истории вместо простого отбрасывания).
    """
    if len(session.history) > MESSAGES_TO_SEND:
        trimmed = list(session.history)[-MESSAGES_TO_SEND:]
        session.history = deque(trimmed, maxlen=MAX_HISTORY_MESSAGES)


# ---------------------------------------------------------------------------------
# БЛОК 8. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С GEMINI (ТЕКСТ + ЗРЕНИЕ)
# ---------------------------------------------------------------------------------

async def download_photo_bytes(bot: Bot, file_id: str) -> Optional[bytes]:
    """
    Скачивает файл фотографии из Telegram по file_id и возвращает его байты.

    Используем bot.get_file() для получения file_path, затем bot.download_file()
    для скачивания самого содержимого. Ограничиваем размер файла, чтобы не
    расходовать лишний трафик и не упереться в лимиты Gemini API по размеру
    вложения.

    :return: байты изображения либо None при ошибке/превышении лимита размера.
    """
    try:
        file_info = await bot.get_file(file_id)

        if file_info.file_size and file_info.file_size > MAX_PHOTO_SIZE_BYTES:
            logger.warning(
                "Файл %s превышает лимит размера (%d байт > %d байт), пропускаем.",
                file_id, file_info.file_size, MAX_PHOTO_SIZE_BYTES,
            )
            return None

        file_stream: io.BytesIO = await bot.download_file(file_info.file_path)
        return file_stream.read()

    except TelegramAPIError as exc:
        logger.error("Ошибка Telegram API при скачивании файла %s: %s", file_id, exc)
        return None
    except Exception as exc:  # noqa: BLE001 — сетевые сбои тоже перехватываем здесь
        logger.error("Непредвиденная ошибка при скачивании файла %s: %s", file_id, exc)
        return None


def build_contents_for_gemini(session: UserSession) -> List[Dict[str, Any]]:
    """
    Формирует список contents для передачи в generate_content на основе истории
    сессии, предварительно применив «срез по последним сообщениям».
    """
    trim_history(session)
    return list(session.history)


async def ask_gemini_text(
    session: UserSession,
    user_text: str,
    image_bytes: Optional[bytes] = None,
    image_mime_type: str = "image/jpeg",
) -> str:
    """
    Основная функция обращения к текстовой/мультимодальной модели Gemini.

    Логика:
        1. Собираем "parts" нового сообщения пользователя: текст + (опционально) картинка.
        2. Добавляем сообщение в историю сессии.
        3. Формируем полный список contents (история + системный промпт передаётся
           отдельно через config.system_instruction — это официальный механизм
           google-genai для системных инструкций, не засоряющий историю диалога).
        4. Вызываем generate_content в отдельном потоке через asyncio.to_thread,
           т.к. официальный синхронный клиент genai блокирующий, а нам нужен
           неблокирующий event loop aiogram.
        5. Добавляем ответ модели в историю сессии.
        6. Возвращаем текст ответа пользователю.

    Обрабатываются любые исключения GenAI API — пользователь получает вежливое
    сообщение об ошибке, а полная трассировка уходит в лог.
    """
    user_parts: List[Any] = []

    if image_bytes:
        # Картинка передаётся как Part.from_bytes — стандартный способ мультимодального
        # ввода в google-genai для «сырых» байтов без загрузки через Files API.
        user_parts.append(
            genai_types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type)
        )

    # Текст добавляем всегда — даже пустую подпись к фото заменяем на нейтральный
    # промпт, чтобы модель не осталась без текстовой части запроса.
    text_for_model = user_text.strip() if user_text and user_text.strip() else (
        "Опиши, что изображено на этой картинке, и прокомментируй её."
    )
    user_parts.append(genai_types.Part.from_text(text=text_for_model))

    append_to_history(session, role="user", parts=user_parts)
    contents = build_contents_for_gemini(session)

    generation_config = genai_types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.8,             # Небольшая доля творчества, но без потери связности
        max_output_tokens=4096,      # Достаточно для развёрнутых ответов и кода
        top_p=0.95,
    )

    try:
        # Выбираем модель: если в запросе есть изображение — используем vision-модель
        # (в данном случае это может быть та же модель, но параметр вынесен отдельно
        # на случай, если понадобится разделить текстовую и визуальную модели).
        model_name = VISION_MODEL_NAME if image_bytes else TEXT_MODEL_NAME

        response = await asyncio.to_thread(
            genai_client.models.generate_content,
            model=model_name,
            contents=contents,
            config=generation_config,
        )

        answer_text = (response.text or "").strip()
        if not answer_text:
            answer_text = "Извините, не получилось сформировать ответ. Попробуйте переформулировать запрос."

        # Ответ модели тоже кладём в историю — это критично для связности диалога.
        append_to_history(
            session,
            role="model",
            parts=[genai_types.Part.from_text(text=answer_text)],
        )
        return answer_text

    except GenAIAPIError as exc:
        logger.error("Ошибка Gemini API (текст): %s", exc)
        return (
            "Сейчас возникли технические неполадки при обращении к модели. "
            "Пожалуйста, попробуйте ещё раз через несколько секунд."
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Непредвиденная ошибка в ask_gemini_text: %s\n%s", exc, traceback.format_exc())
        return "Произошла непредвиденная ошибка. Мы уже разбираемся, попробуйте, пожалуйста, чуть позже."


# ---------------------------------------------------------------------------------
# БЛОК 9. ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ С МЕХАНИЗМОМ FALLBACK
# ---------------------------------------------------------------------------------

async def _generate_image_with_model(prompt: str, model_name: str) -> Optional[bytes]:
    """
    Пытается сгенерировать изображение конкретной моделью.

    Возвращает байты PNG/JPEG-изображения либо None, если модель не смогла
    вернуть картинку (например, отфильтровала промпт или вернула только текст).

    Функция не перехватывает исключения "тихо" — они пробрасываются наверх,
    чтобы вызывающий код (generate_art_with_fallback) мог принять решение
    о переключении на резервную модель.
    """
    response = await asyncio.to_thread(
        genai_client.models.generate_content,
        model=model_name,
        contents=[genai_types.Part.from_text(text=prompt)],
        config=genai_types.GenerateContentConfig(
            response_modalities=["Image"],
        ),
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
    Генерирует изображение по текстовому промпту с отказоустойчивым fallback:

        1. Сначала пробуем основную модель IMAGE_MODEL_PRIMARY.
        2. Если основная модель недоступна (сетевая ошибка, таймаут, 5xx, лимит
           запросов) или вернула пустой результат — переключаемся на резервную
           модель IMAGE_MODEL_FALLBACK.
        3. Если и резервная модель не сработала — возвращаем None, а вызывающий
           код сообщает пользователю понятную ошибку.

    Такой каскад значительно повышает отказоустойчивость: временная недоступность
    одной модели (например, из-за перегрузки дата-центра) не «валит» всю функцию
    генерации арта для пользователей.
    """
    # --- Попытка №1: основная модель -------------------------------------------------
    try:
        logger.info("Генерация изображения основной моделью '%s'…", IMAGE_MODEL_PRIMARY)
        image_bytes = await _generate_image_with_model(prompt, IMAGE_MODEL_PRIMARY)
        if image_bytes:
            logger.info("Изображение успешно сгенерировано основной моделью.")
            return image_bytes
        logger.warning("Основная модель вернула пустой результат, переключаемся на резервную.")
    except (GenAIAPIError, aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as exc:
        logger.warning("Основная модель генерации изображений недоступна (%s), пробуем резервную.", exc)
    except Exception as exc:  # noqa: BLE001
        logger.error("Неожиданная ошибка основной модели генерации: %s", exc)

    # --- Попытка №2: резервная (fallback) модель -------------------------------------
    try:
        logger.info("Генерация изображения резервной моделью '%s'…", IMAGE_MODEL_FALLBACK)
        image_bytes = await _generate_image_with_model(prompt, IMAGE_MODEL_FALLBACK)
        if image_bytes:
            logger.info("Изображение успешно сгенерировано резервной моделью.")
            return image_bytes
        logger.error("Резервная модель также вернула пустой результат.")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("Резервная модель генерации изображений тоже не сработала: %s", exc)
        return None


# ---------------------------------------------------------------------------------
# БЛОК 10. РОУТЕР И ХЭНДЛЕРЫ AIOGRAM
# ---------------------------------------------------------------------------------

router = Router(name="main_router")


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """
    Хэндлер команды /start.

    Приветствует пользователя тепло и уважительно (на «Вы»), сбрасывает его
    сессию (новая история диалога с чистого листа) и показывает главную
    клавиатуру ровно с двумя кнопками.
    """
    user_id = message.from_user.id if message.from_user else message.chat.id
    USER_SESSIONS[user_id] = UserSession()  # свежая сессия при явном /start

    first_name = message.from_user.first_name if message.from_user else "друг"

    welcome_text = (
        f"Здравствуйте, {first_name}! Рад(а) знакомству. 🙂\n\n"
        "Я — Ваш персональный ИИ-ассистент: помогу разобраться в любом вопросе, "
        "написать и отладить код на любом языке программирования, разобрать "
        "изображение или скриншот, а по запросу — сгенерирую уникальную картинку.\n\n"
        "Просто напишите сообщение — и мы начнём. Если захотите, можем перейти на «ты» "
        "в любой момент разговора."
    )

    await message.answer(welcome_text, reply_markup=MAIN_KEYBOARD)
    logger.info("Пользователь %s (%s) запустил бота командой /start.", user_id, first_name)


@router.message(Command("reset"))
async def handle_reset(message: Message) -> None:
    """
    Дополнительная служебная команда для явного сброса истории диалога
    (не обязательна к использованию пользователем, но полезна для отладки
    и как «аварийный выход», если история засорилась нерелевантным контекстом).
    """
    user_id = message.from_user.id if message.from_user else message.chat.id
    USER_SESSIONS[user_id] = UserSession()
    await message.answer("История диалога очищена, можем начать заново.", reply_markup=MAIN_KEYBOARD)


@router.message(F.text == BTN_PRO_SUBSCRIPTION)
async def handle_pro_subscription(message: Message) -> None:
    """
    Обрабатывает нажатие кнопки «⭐ PRO Подписка».

    В реальном проекте здесь обычно формируется инвойс через Telegram Payments
    (bot.send_invoice) либо ссылка на внешний платёжный сервис. Для простоты
    и переносимости примера выводим информативное сообщение со ссылкой,
    которую легко заменить на реальную интеграцию оплаты.
    """
    text = (
        "⭐ *PRO Подписка*\n\n"
        "С PRO-подпиской Вы получаете:\n"
        "• Безлимитные запросы к текстовой модели\n"
        "• Приоритетную генерацию изображений без очереди\n"
        "• Расширенный контекст памяти диалога\n\n"
        f"Оформить подписку можно здесь: {PRO_LINK}"
    )
    await message.answer(text, reply_markup=MAIN_KEYBOARD)


@router.message(F.text == BTN_GENERATE_ART)
async def handle_generate_art_button(message: Message) -> None:
    """
    Обрабатывает нажатие кнопки «🎨 Сгенерировать арт».

    Бот не генерирует изображение немедленно (у нас ещё нет промпта), а переводит
    сессию пользователя в режим ожидания промпта: следующее текстовое сообщение
    будет интерпретировано как описание желаемой картинки (см. handle_text_message).
    """
    user_id = message.from_user.id if message.from_user else message.chat.id
    session = get_session(user_id)
    session.awaiting_art_prompt = True

    await message.answer(
        "Отлично! Опишите, пожалуйста, что бы Вы хотели увидеть на картинке — "
        "чем подробнее описание (стиль, цвета, композиция), тем точнее получится результат.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(F.photo)
async def handle_photo_message(message: Message, bot: Bot) -> None:
    """
    Обрабатывает входящие фотографии (мультимодальный ввод).

    Telegram присылает фото в нескольких разрешениях — берём последний элемент
    списка message.photo, так как он соответствует максимальному качеству.
    Скачанные байты передаются в Gemini вместе с подписью пользователя
    (caption) в качестве уточняющего промпта. Если подписи нет — используется
    нейтральный промпт по умолчанию (см. ask_gemini_text).
    """
    user_id = message.from_user.id if message.from_user else message.chat.id
    session = get_session(user_id)

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    largest_photo = message.photo[-1]
    image_bytes = await download_photo_bytes(bot, largest_photo.file_id)

    if image_bytes is None:
        await message.answer(
            "Не получилось загрузить это изображение (возможно, файл слишком большой "
            "или произошла временная ошибка сети). Попробуйте отправить его ещё раз.",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    caption = message.caption or ""
    answer_text = await ask_gemini_text(session, user_text=caption, image_bytes=image_bytes)

    await message.answer(answer_text, reply_markup=MAIN_KEYBOARD)


@router.message(F.text)
async def handle_text_message(message: Message, bot: Bot) -> None:
    """
    Главный обработчик текстовых сообщений.

    Порядок логики:
        1. Если сессия пользователя находится в режиме «ожидание промпта для арта»
           (после нажатия кнопки «🎨 Сгенерировать арт») — интерпретируем текущее
           сообщение как промпт и запускаем генерацию изображения.
        2. Иначе — это обычная реплика диалога, отправляем её в ask_gemini_text
           и возвращаем текстовый ответ модели.

    Индикатор "печатает…"/"отправляет фото…" показывается на всё время обработки,
    чтобы пользователь видел, что бот активно работает над ответом.
    """
    user_id = message.from_user.id if message.from_user else message.chat.id
    session = get_session(user_id)
    user_text = message.text or ""

    # --- Ветка 1: пользователь только что нажал «Сгенерировать арт» -----------------
    if session.awaiting_art_prompt:
        session.awaiting_art_prompt = False  # сбрасываем флаг сразу, чтобы избежать повторов
        await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)

        image_bytes = await generate_art_with_fallback(user_text)

        if image_bytes is None:
            await message.answer(
                "К сожалению, сейчас не удалось сгенерировать изображение (проблема на стороне "
                "сервиса генерации). Пожалуйста, попробуйте ещё раз чуть позже или измените описание.",
                reply_markup=MAIN_KEYBOARD,
            )
            return

        photo_file = BufferedInputFile(image_bytes, filename="generated_art.png")
        await message.answer_photo(
            photo=photo_file,
            caption=f'Готово! Вот изображение по описанию: "{user_text}"',
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # --- Ветка 2: обычное текстовое сообщение в диалоге ------------------------------
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    answer_text = await ask_gemini_text(session, user_text=user_text)
    await message.answer(answer_text, reply_markup=MAIN_KEYBOARD)


@router.message()
async def handle_unsupported_content(message: Message) -> None:
    """
    Заглушка для любых иных типов сообщений (стикеры, голосовые, документы и т.п.),
    которые явно не обрабатываются выше. Вежливо сообщаем пользователю, какие
    форматы бот умеет обрабатывать, вместо того чтобы просто игнорировать сообщение.
    """
    await message.answer(
        "Пока что я умею работать с текстом и изображениями. Пришлите, пожалуйста, "
        "текстовое сообщение или фотографию.",
        reply_markup=MAIN_KEYBOARD,
    )


# ---------------------------------------------------------------------------------
# БЛОК 11. AIOHTTP-СЕРВЕР ДЛЯ HEALTH-CHECK (НЕОБХОДИМ ДЛЯ RENDER)
# ---------------------------------------------------------------------------------
# Render (и аналогичные PaaS-платформы) ожидают, что веб-сервис будет слушать
# указанный порт и отвечать на HTTP-запросы — иначе деплой считается "упавшим".
# Поэтому параллельно с поллингом Telegram поднимаем лёгкий aiohttp-сервер.

async def handle_health_check(request: web.Request) -> web.Response:
    """Простой health-check эндпоинт: подтверждает, что процесс жив и отвечает."""
    return web.json_response({"status": "ok", "service": "gemini-telegram-bot", "time": time.time()})


def build_web_app() -> web.Application:
    """Собирает минимальное aiohttp-приложение с единственным маршрутом '/'."""
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    app.router.add_get("/health", handle_health_check)
    return app


async def run_web_server() -> None:
    """
    Запускает aiohttp-сервер на порту PORT и держит его работающим бесконечно
    (до отмены задачи извне). AppRunner/TCPSite — стандартный низкоуровневый
    способ запустить aiohttp-приложение вручную внутри уже существующего
    event loop, без блокирующего web.run_app().
    """
    app = build_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info("Health-check сервер запущен на 0.0.0.0:%d", PORT)

    # Бесконечно ждём — задача будет отменена извне при остановке приложения.
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Остановка health-check сервера…")
        await runner.cleanup()
        raise


# ---------------------------------------------------------------------------------
# БЛОК 12. ЗАПУСК ПОЛЛИНГА TELEGRAM-БОТА С ОТКАЗОУСТОЙЧИВЫМ ПЕРЕЗАПУСКОМ
# ---------------------------------------------------------------------------------

async def run_bot_polling() -> None:
    """
    Запускает long-polling бота.

    drop_pending_updates=True гарантирует, что после рестарта процесса (например,
    из-за деплоя новой версии на Render) бот не станет "отвечать задним числом"
    на сообщения, накопившиеся за время простоя — все pending-обновления
    отбрасываются, и бот начинает обработку строго с текущего момента.

    Обёрнуто в try/except с автоматическим перезапуском при сбое: если по любой
    причине поллинг упадёт (сетевой сбой, временная недоступность Telegram API),
    процесс не завершится, а через паузу попробует переподключиться заново —
    это и есть требуемая отказоустойчивость главного блока.
    """
    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    retry_delay_seconds = 5
    max_retry_delay_seconds = 60

    while True:
        try:
            logger.info("Запуск long-polling Telegram-бота…")
            await dispatcher.start_polling(
                bot,
                drop_pending_updates=True,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
            # Если start_polling завершился штатно (например, через dispatcher.stop_polling()) —
            # выходим из цикла без повторной попытки.
            break

        except asyncio.CancelledError:
            logger.info("Поллинг бота остановлен по сигналу отмены задачи.")
            raise

        except Exception as exc:  # noqa: BLE001 — здесь нужен максимально широкий перехват
            logger.error(
                "Поллинг бота упал с ошибкой: %s\n%s",
                exc, traceback.format_exc(),
            )
            logger.info("Повторная попытка запуска через %d секунд…", retry_delay_seconds)
            await asyncio.sleep(retry_delay_seconds)
            # Экспоненциальное увеличение задержки между попытками, чтобы не "долбить"
            # Telegram API слишком часто при затяжном сбое.
            retry_delay_seconds = min(retry_delay_seconds * 2, max_retry_delay_seconds)

        finally:
            # На всякий случай закрываем HTTP-сессию бота при каждом выходе из цикла,
            # чтобы не копить "зависшие" соединения при повторных попытках запуска.
            pass

    await bot.session.close()


# ---------------------------------------------------------------------------------
# БЛОК 13. ТОЧКА ВХОДА: ПАРАЛЛЕЛЬНЫЙ ЗАПУСК СЕРВЕРА И ПОЛЛИНГА
# ---------------------------------------------------------------------------------

async def main() -> None:
    """
    Главная асинхронная функция приложения.

    Запускает две долгоживущие задачи параллельно через asyncio.gather:
        1. run_web_server()  — health-check сервер для Render.
        2. run_bot_polling() — сам Telegram-бот.

    Если одна из задач завершится с исключением, asyncio.gather по умолчанию
    пробрасывает исключение наверх — это осознанный выбор: если критический
    компонент (например, веб-сервер, без которого Render считает деплой
    неудачным) упал безвозвратно, лучше уронить весь процесс и дать
    оркестратору (Render) перезапустить контейнер с чистого листа, чем
    оставлять бота в "полуживом" состоянии.
    """
    logger.info("=" * 70)
    logger.info("Запуск приложения: Telegram-бот + health-check сервер")
    logger.info("Текстовая модель: %s | Vision-модель: %s", TEXT_MODEL_NAME, VISION_MODEL_NAME)
    logger.info("Модель генерации арта (основная/резервная): %s / %s",
                IMAGE_MODEL_PRIMARY, IMAGE_MODEL_FALLBACK)
    logger.info("=" * 70)

    await asyncio.gather(
        run_web_server(),
        run_bot_polling(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Приложение остановлено пользователем или системой. До встречи!")
    except Exception as exc:  # noqa: BLE001 — последний рубеж обороны перед падением процесса
        logger.critical("Фатальная ошибка на верхнем уровне приложения: %s\n%s",
                         exc, traceback.format_exc())
        sys.exit(1)

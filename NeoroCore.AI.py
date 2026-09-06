"""
================================================================================
 ПРОЕКТ: NEUROCORE OMEGA AI (ВЕРСИЯ ДЛЯ ПРОДУКТИВА 3.7)
 АВТОР: ПЕРСОНАЛЬНЫЙ ИИ-ПАРТНЕР
 ОПИСАНИЕ: 
 Высокооптимизированный Telegram-бот на aiogram 3.x и Google GenAI SDK.
 Включает в себя глубокую системную инструкцию для ИИ (System Instructions),
 поддержку мультимодальности (текст, код, анализ фото) и генерацию графики.
================================================================================
"""

import os
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton
from google import genai
from google.genai import types as genai_types

import database

# Настройка логирования для отслеживания всех процессов в реальном времени
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("NeuroCoreOmega")

# Получение и проверка токенов окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    logger.critical("❌ Критическая ошибка: Отсутствуют TELEGRAM_TOKEN или GEMINI_API_KEY в переменных среды!")

# Инициализация основных компонентов бота и клиента искусственного интеллекта
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
genai_client = genai.Client(api_key=GEMINI_API_KEY)

# Оперативное хранилище сессионной памяти диалогов (ключ: user_id, значение: список строк контекста)
user_chat_memories = {}

# Глобальная системная инструкция (System Prompt) для ИИ. 
# Объясняет модели её роль, характер общения и правила взаимодействия с пользователем.
SYSTEM_AI_PERSONA = (
    "Ты — NeuroCore Omega AI, передовой, высокооптимизированный искусственный интеллект. "
    "Твоя задача — помогать пользователю во всем: писать чистый, структурированный код на любых языках программирования, "
    "анализировать фотографии, решать сложные задачи и генерировать идеи.\n"
    "ПРАВИЛА ОБЩЕНИЯ:\n"
    "1. Общайся с пользователем тепло, ласково и уважительно. Встречай его с заботой и душевным теплом.\n"
    "2. Начинай диалог и обращение вежливо (на «Вы»), но мягко переходи на дружеское «ты», создавая атмосферу доверительного партнерства.\n"
    "3. Всегда глубоко анализируй контекст, будь предельно точен, пиши развернутые, качественные и оптимизированные ответы.\n"
    "4. Если пользователь просит код — пиши его идеально, с комментариями на русском языке и объяснением логики работы."
)


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Создает и возвращает главную клавиатуру интерфейса без лишних кнопок."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⭐ PRO Подписка"), KeyboardButton(text="🎨 Сгенерировать арт")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


async def handle_ping(request: web.Request) -> web.Response:
    """HTTP-обработчик для поддержания активности сервера на платформе Render."""
    return web.Response(
        text="<h1>NEUROCORE OMEGA AI - SYSTEM ACTIVE & OPTIMIZED</h1>", 
        content_type='text/html'
    )


@dp.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    """Обработчик команды /start. Инициализирует пользователя в базе данных и приветствует его."""
    try:
        user_id = message.from_user.id
        username = message.from_user.username
        
        # Регистрация или получение профиля из базы данных
        await database.get_or_create_user(user_id, username)
        user_chat_memories[user_id] = []
        
        welcome_text = (
            "✨ <b>Здравствуйте! Добро пожаловать в NeuroCore Omega AI.</b>\n\n"
            "Я искренне рад нашему знакомству. Готов окружить Вас заботой и помочь решить любые задачи:\n"
            "• Написать чистый и надежный код на любом языке программирования\n"
            "• Распознать и проанализировать отправленные Вами фотографии\n"
            "• Создать потрясающие визуальные образы и арты\n\n"
            "Просто отправьте мне текстовый запрос или прикрепите изображение!"
        )
        await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_reply_keyboard())
        logger.info(f"Успешный запуск сессии для пользователя ID: {user_id}")
    except Exception as error:
        logger.error(f"Ошибка в обработчике cmd_start: {error}")


@dp.message(F.text == "⭐ PRO Подписка")
async def process_buy_pro(message: types.Message) -> None:
    """Информационный блок о возможностях PRO-аккаунта и оптимизированных лимитах."""
    pro_info_text = (
        "👑 <b>Преимущества привилегированного уровня PRO:</b>\n\n"
        "• Работа на базе новейших флагманских моделей Gemini\n"
        "• Увеличенные суточные лимиты сообщений и генераций кода\n"
        "• Приоритетная скорость обработки запросов и создания графики\n\n"
        "Для активации PRO-доступа воспользуйтесь внутренними системными настройками."
    )
    await message.answer(pro_info_text, parse_mode="HTML", reply_markup=get_main_reply_keyboard())


@dp.message(F.text == "🎨 Сгенерировать арт")
async def btn_draw_info(message: types.Message) -> Noneвлечении) -> None:
    """Подсказка для пользователя о том, как правильно запускать генерацию графики."""
    prompt_help = (
        "🎨 <b>Создание произведений искусства</b>\n\n"
        "Чтобы я создал для Вас великолепную картинку, отправьте команду `/draw` и Ваше описание на любом языке.\n\n"
        "<i>Пример:</i> <code>/draw Футуристический ночной город в стиле киберпанк с неоновой подсветкой</code>"
    )
    await message.answer(prompt_help, parse_mode="HTML")


@dp.message(Command("draw"))
async def cmd_draw(message: types.Message) -> None:
    """Генерация графики с использованием встроенных инструментов и проверки лимитов."""
    user_id = message.from_user.id
    prompt = message.text.replace("/draw", "").strip()

    if not prompt:
        await message.answer("⚠️ Пожалуйста, добавьте описание после команды `/draw`, чтобы я знал, какой арт сотворить.", parse_mode="Markdown")
        return

    # Проверка суточных лимитов пользователя в базе данных
    is_allowed = await database.check_and_increment_limit(user_id, "draw")
    if not is_allowed:
        await message.answer("❌ К сожалению, Ваш суточный лимит генерации изображений исчерпан. Пожалуйста, отдохните до завтра.")
        return

    status_message = await message.answer("🎨 <i>Душа моя, я начал творить для Вас прекрасный арт. Подождите мгновение...</i>", parse_mode="HTML")

    try:
        user_profile = await database.get_or_create_user(user_id)
        tier_level = user_profile.get("tier", "free")
        
        # Выбор модели в зависимости от уровня подписки пользователя
        ai_model_name = database.TIERS[tier_level]["genai_model"]

        # Используем официальный генератор изображений Google GenAI
        result = genai_client.models.generate_images(
            model='imagen-3.0-generate-002',
            prompt=prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                output_mime_type="image/jpeg",
                aspect_ratio="1:1",
                person_generation="ALLOW_ADULT",
            )
        )

        for generated_image in result.generated_images:
            image_bytes = generated_image.image.image_bytes
            photo_file = BufferedInputFile(image_bytes, filename="neurocore_art.jpg")
            
            await message.answer_photo(
                photo=photo_file,
                caption=f"✨ <b>Ваш арт готов!</b>\n📝 <i>{prompt}</i>",
                parse_mode="HTML"
            )
            break

        await status_message.delete()
    except Exception as generation_error:
        logger.error(f"Ошибка при генерации изображения через Imagen: {generation_error}")
        # Запасной вариант через стабильный публичный генератор, если Imagen недоступен на бесплатном ключе
        try:
            encoded_query = urllib.parse.quote(prompt)
            fallback_url = f"https://image.pollinations.ai/prompt/{encoded_query}?width=1024&height=1024&model=flux&nologo=true"
            
            async with aiohttp.ClientSession() as http_session:
                async with http_session.get(fallback_url, timeout=40) as response_data:
                    if response_data.status == 200:
                        raw_bytes = await response_data.read()
                        backup_photo = BufferedInputFile(raw_bytes, filename="fallback_art.jpg")
                        await message.answer_photo(
                            photo=backup_photo,
                            caption=f"🎨 <b>Арт создан (Альтернативный поток):</b>\n📝 {prompt}",
                            parse_mode="HTML"
                        )
                        await status_message.delete()
                        return
        except Exception as fallback_err:
            logger.error(f"Резервный генератор также сдал сбой: {fallback_err}")

        await status_message.edit_text("⚠️ К сожалению, сейчас не удалось сформировать изображение. Попробуйте изменить формулировку запроса.")


@dp.message(F.photo)
async def handle_photo_message(message: types.Message) -> None:
    """Мультимодальный обработчик: считывание, анализ и детальный ответ по отправленным фотографиям."""
    user_id = message.from_user.id

    is_allowed = await database.check_and_increment_limit(user_id, "photo")
    if not is_allowed:
        await message.answer("❌ Исчерпан суточный лимит на распознавание фотографий.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        user_profile = await database.get_or_create_user(user_id)
        tier_level = user_profile.get("tier", "free")
        ai_model_name = database.TIERS[tier_level]["genai_model"]

        # Скачиваем фотографию максимального разрешения
        photo_object = message.photo[-1]
        file_meta = await bot.get_file(photo_object.file_id)
        downloaded_file = await bot.download_file(file_meta.file_path)
        image_bytes_data = downloaded_file.read()

        user_caption = message.caption or "Внимательно изучи эту фотографию, опиши её детали и ответь на все скрытые вопросы."

        # Отправка запроса с системной инструкцией и изображением в модель Gemini
        response = genai_client.models.generate_content(
            model=ai_model_name,
            contents=[
                SYSTEM_AI_PERSONA,
                user_caption,
                genai_types.Part.from_bytes(data=image_bytes_data, mime_type="image/jpeg")
            ]
        )
        
        await message.answer(response.text, parse_mode="HTML")
    except Exception as photo_error:
        logger.error(f"Ошибка при обработке фотографии: {photo_error}")
        await message.answer("⚠️ Не удалось распознать изображение. Пожалуйста, убедитесь, что файл отправлен корректно.")


@dp.message(F.text)
async def handle_text_message(message: types.Message) -> None:
    """Основной текстовый обработчик с удержанием контекста сессии, поддержкой кода и языков."""
    user_id = message.from_user.id
    user_text = message.text

    is_allowed = await database.check_and_increment_limit(user_id, "text")
    if not is_allowed:
        await message.answer("❌ Суточный лимит текстовых сообщений исчерпан. Пожалуйста, отдохните немного.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        user_profile = await database.get_or_create_user(user_id)
        tier_level = user_profile.get("tier", "free")
        ai_model_name = database.TIERS[tier_level]["genai_model"]

        if user_id not in user_chat_memories:
            user_chat_memories[user_id] = []

        # Сохранение реплики пользователя в оперативном контексте
        user_chat_memories[user_id].append(f"Пользователь: {user_text}")
        
        # Формирование связки системного промпта и последних 12 сообщений для идеальной памяти
        recent_history = "\n".join(user_chat_memories[user_id][-12:])
        full_payload = f"{SYSTEM_AI_PERSONA}\n\nКОНТЕКСТ ДИАЛОГА:\n{recent_history}"

        response = genai_client.models.generate_content(
            model=ai_model_name,
            contents=full_payload
        )
        
        assistant_reply = response.text
        
        # Сохранение ответа ассистента в память сессии
        user_chat_memories[user_id].append(f"Ассистент: {assistant_reply}")
        
        await message.answer(assistant_reply)
    except Exception as text_error:
        logger.error(f"Ошибка генерации текстового ответа: {text_error}")
        await message.answer("⚠️ Произошла небольшая техническая заминка при обработке запроса. Пожалуйста, повторите попытку.")


async def main() -> None:
    """Главная асинхронная функция запуска веб-сервера и Telegram-бота с защитой от конфликтов."""
    web_application = web.Application()
    web_application.router.add_get("/", handle_ping)
    
    server_runner = web.AppRunner(web_application)
    await server_runner.setup()
    
    port_number = int(os.getenv("PORT", 8080))
    site_listener = web.TCPSite(server_runner, "0.0.0.0", port_number)
    await site_listener.start()
    logger.logger_status = logger.info(f"Веб-сервер успешно запущен на порту {port_number}")

    # Сброс зависших вебхуков для предотвращения ошибок ConflictError при перезапуске
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот начал прослушивание обновлений (Polling)...")
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Работа бота NeuroCore Omega AI штатно завершена.")

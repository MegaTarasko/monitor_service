#!/usr/bin/env python3
import os
import logging
import subprocess
import shutil
import asyncio
import time
import math
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

#хранение токенов
from dotenv import load_dotenv
#Загрузка токена
load_dotenv()
token = os.getenv('TOKEN')
admin_id = os.getenv('ADMIN_ID')
#Настройки
BOT_TOKEN = token
# ========== НАСТРОЙКИ ПРОКСИ ==========
PROXY_HOST = os.getenv('PROXY_HOST')
PROXY_PORT = os.getenv('PROXY_PORT')
PROXY_USER = os.getenv('PROXY_USER')
PROXY_PASS = os.getenv('PROXY_PASS')
PROXY_PORT = int(PROXY_PORT)
# Формируем URL прокси с аутентификацией
PROXY_URL = f'http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}'
# ======================================

# Настройка логирования
# logging.basicConfig(
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#     level=logging.INFO
# )
logger = logging.getLogger(__name__)

# Временное хранилище для данных пользователей
user_data = {}

# Поддерживаемые платформы
SUPPORTED_PLATFORMS = {
    'vk.com': 'VK Video',
    'vkvideo.ru': 'VK Video',
    'rutube.ru': 'Rutube'
}


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "🤖 Бот для скачивания видео\n\n"
        "📹 Поддерживаемые платформы:\n"
        "• VK Video\n"
        "• Rutube\n\n"
        "💾 Автоматическая оптимизация видео\n"
        "🎬 Отправка как видео с встроенным плеером\n"
        "🔄 Сжатие больших файлов\n\n"
        "Просто отправьте мне ссылку на видео!"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    platforms_list = "\n".join([f"• {name}" for name in SUPPORTED_PLATFORMS.values()])

    await update.message.reply_text(
        f"📖 Как использовать бота:\n\n"
        f"1. Отправьте ссылку на видео\n"
        f"2. Выберите качество\n"
        f"3. Дождитесь скачивания\n"
        f"4. После скачивания выберите действие\n\n"
        f"📹 Поддерживаемые платформы:\n{platforms_list}\n\n"
        f"🎬 Видео отправляются с встроенным плеером\n"
        f"💾 Автоматическая оптимизация и сжатие\n"
        f"✂️ Разделение больших файлов на части\n"
        f"📁 Сохранение в личной папке пользователя\n"
        f"⚠️ YouTube временно не поддерживается\n"
        f"🗑️ Файлы автоматически удаляются через 1 час"
    )


async def platforms_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для показа поддерживаемых платформ"""
    platforms_list = "\n".join([f"• {name}" for name in sorted(set(SUPPORTED_PLATFORMS.values()))])

    await update.message.reply_text(
        f"📹 Поддерживаемые платформы:\n\n{platforms_list}\n\n"
        f"🔗 Просто отправьте ссылку с любой из этих платформ!\n\n"
        f"⚠️ YouTube временно не поддерживается"
    )


def is_supported_platform(url: str) -> bool:
    """Проверяет поддерживается ли платформа"""
    return any(domain in url for domain in SUPPORTED_PLATFORMS.keys())


def get_platform_name(url: str) -> str:
    """Возвращает название платформы по URL"""
    for domain, name in SUPPORTED_PLATFORMS.items():
        if domain in url:
            return name
    return "Неизвестная платформа"


async def handle_video_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ссылок на видео"""
    url = update.message.text.strip()
    user_id = update.message.from_user.id
    username = update.message.from_user.username or f"user_{user_id}"

    logger.info(f"User {user_id} ({username}) requested: {url}")

    # Проверяем, что это ссылка
    if not url.startswith(('http://', 'https://')):
        await update.message.reply_text("❌ Это не похоже на ссылку. Отправьте корректный URL.")
        return

    # Проверяем поддержку платформы
    if not is_supported_platform(url):
        await update.message.reply_text(
            "❌ Эта платформа не поддерживается\n\n"
            "📹 Используйте команду /platforms чтобы увидеть список поддерживаемых платформ.\n\n"
        )
        return

    # Автоматические замены URL для лучшей совместимости
    url = normalize_url(url)

    # Сохраняем URL для пользователя
    user_data[user_id] = {
        'url': url,
        'step': 'quality_selection',
        'platform': get_platform_name(url),
        'username': username
    }

    # Предлагаем выбрать качество
    keyboard = [
        [
            InlineKeyboardButton("🎥 Лучшее качество", callback_data="quality_best"),
            InlineKeyboardButton("⚖️ Сбалансированное", callback_data="quality_720")
        ],
        [
            InlineKeyboardButton("📱 Для телефона (480p)", callback_data="quality_480"),
            InlineKeyboardButton("🎵 Только аудио", callback_data="quality_audio")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    platform_name = get_platform_name(url)
    await update.message.reply_text(
        f"🎬 Ссылка с {platform_name} принята!\nВыберите качество видео:",
        reply_markup=reply_markup
    )


def normalize_url(url: str) -> str:
    """Нормализует URL для лучшей совместимости"""
    # Заменяем vkvideo.ru на vk.com
    if 'vkvideo.ru' in url:
        url = url.replace('vkvideo.ru', 'vk.com')

    # Заменяем x.com на twitter.com
    if 'x.com' in url:
        url = url.replace('x.com', 'twitter.com')

    return url


async def handle_quality_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора качества"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    quality = query.data.replace('quality_', '')

    if user_id not in user_data:
        await query.edit_message_text("❌ Сессия устарела. Отправьте ссылку заново.")
        return

    url = user_data[user_id]['url']
    platform = user_data[user_id]['platform']
    user_data[user_id]['quality'] = quality
    user_data[user_id]['step'] = 'downloading'

    quality_names = {
        'best': 'лучшее качество',
        '720': '720p (HD)',
        '480': '480p',
        'audio': 'только аудио'
    }

    await query.edit_message_text(
        f"⏬ Начинаю скачивание с {platform}...\n"
        f"💾 Качество: {quality_names.get(quality, quality)}\n"
        f"⏳ Это может занять несколько минут..."
    )

    # Запускаем скачивание
    asyncio.create_task(download_video(user_id, url, quality, context))


async def download_video(user_id: int, url: str, quality: str, context: ContextTypes.DEFAULT_TYPE):
    """Скачивание видео в фоновом режиме"""
    try:
        # Создаем папку для пользователя
        user_dir = f"/home/taras/video_downloads/user_{user_id}"
        os.makedirs(user_dir, exist_ok=True)

        # ИСПРАВЛЕННЫЕ настройки качества
        quality_presets = {
            'best': {
                'format': 'res:1080,fps',
                'description': 'Лучшее качество (до 1080p)'
            },
            '720': {
                'format': 'res:720,fps',
                'description': 'HD качество (720p)'
            },
            '480': {
                'format': 'res:480,fps',
                'description': 'Стандартное качество (480p)'
            },
            'audio': {
                'format': 'bestaudio/best',
                'description': 'Только аудио',
                'audio_params': ['-x', '--audio-format', 'mp3', '--audio-quality', '5']
            }
        }

        preset = quality_presets.get(quality, quality_presets['best'])
        format_selection = preset['format']

        # Базовые параметры
        cmd = [
            "yt-dlp",
            "-o", f"{user_dir}/%(title)s.%(ext)s",
            "-S", format_selection,
            "-f mp4",
            "--no-warnings",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "--retries", "3",
            "--fragment-retries", "3",
            "--socket-timeout", "30",
            url
        ]

        # Добавляем параметры для аудио если нужно
        if quality == 'audio' and 'audio_params' in preset:
            cmd.extend(preset['audio_params'])

        logger.info(f"Скачивание с параметрами: {' '.join(cmd)}")

        # Выполняем скачивание
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode == 0:
            # Ищем скачанный файл
            files = os.listdir(user_dir)
            if files:
                latest_file = max([os.path.join(user_dir, f) for f in files], key=os.path.getctime)

                user_data[user_id]['file_path'] = latest_file
                user_data[user_id]['step'] = 'downloaded'

                file_size = os.path.getsize(latest_file) / (1024 * 1024)
                file_name = os.path.basename(latest_file)

                # Клавиатура действий после скачивания
                keyboard = [
                    [
                        InlineKeyboardButton("🎬 Отправить видео", callback_data="action_send"),
                        InlineKeyboardButton("📁 Сохранить на сервере", callback_data="action_move")
                    ],
                    [
                        InlineKeyboardButton("🗑️ Удалить файл", callback_data="action_delete")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"✅ Видео успешно скачано!\n\n"
                         f"📹 Файл: `{file_name}`\n"
                         f"📊 Размер: {file_size:.2f} MB\n"
                         f"💾 Качество: {preset['description']}\n\n"
                         f"Выберите действие:",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )

                # Запланировать автоматическое удаление через 1 час
                asyncio.create_task(schedule_file_deletion(user_id, latest_file, 3600))
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ Файл не найден после скачивания"
                )
        else:
            error_msg = result.stderr if result.stderr else "Неизвестная ошибка"

            # Улучшенная обработка ошибок
            error_message = await parse_error_message(error_msg, user_data[user_id]['platform'])

            await context.bot.send_message(
                chat_id=user_id,
                text=error_message
            )

    # except subprocess.TimeoutExpired:
    #     await context.bot.send_message(
    #         chat_id=user_id,
    #         text="❌ Таймаут скачивания (10 минут)\n\n"
    #              "Возможно, видео слишком большое или платформа ограничивает скачивание."
    #     )
    except Exception as e:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Неожиданная ошибка:\n`{str(e)}`\n\n"
                 "Попробуйте другую ссылку или повторите позже.",
            parse_mode='Markdown'
        )


async def parse_error_message(error: str, platform: str) -> str:
    """Парсит и форматирует сообщения об ошибках"""
    error_lower = error.lower()

    if "private" in error_lower or "login" in error_lower:
        return f"❌ Видео с {platform} приватное или требует авторизации\n\nДля скачивания нужен доступ к аккаунту."

    elif "geo" in error_lower or "region" in error_lower or "country" in error_lower:
        return f"❌ Видео с {platform} недоступно в вашем регионе\n\nИспользуйте VPN или попробуйте другое видео."

    elif "removed" in error_lower or "deleted" in error_lower or "unavailable" in error_lower:
        return f"❌ Видео с {platform} было удалено или недоступно"

    elif "too large" in error_lower or "size" in error_lower:
        return "❌ Файл слишком большой для скачивания\n\nПопробуйте выбрать меньшее качество."

    else:
        return f"❌ Ошибка скачивания с {platform}:\n`{error[:500]}`\n\nПопробуйте другую ссылку."


async def optimize_video_for_telegram(input_path: str, output_path: str = None) -> str:
    """
    Оптимизирует видео для отправки в Telegram:
    - Сжимает до 720p
    - Уменьшает битрейт
    - Конвертирует в оптимальный формат
    - Удаляет метаданные
    """
    if output_path is None:
        output_path = f"{input_path}_optimized.mp4"

    try:
        # Получаем информацию о исходном видео
        probe_cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", input_path
        ]

        result = subprocess.run(probe_cmd, capture_output=True, text=True)
        video_info = json.loads(result.stdout)

        # Находим видео поток
        video_stream = next((s for s in video_info['streams'] if s['codec_type'] == 'video'), None)

        if not video_stream:
            return input_path  # Не смогли проанализировать, возвращаем оригинал

        original_height = int(video_stream.get('height', 1080))
        original_bitrate = int(video_info['format'].get('bit_rate', 0)) / 1000  # kbps

        # Настройки оптимизации для Telegram
        target_height = min(720, original_height)  # Максимум 720p
        target_bitrate = "2000k"  # Целевой битрейт
        max_bitrate = "2500k"  # Максимальный битрейт
        buffer_size = "5000k"  # Размер буфера

        # Если оригинальное видео уже меньше наших целевых параметров, не оптимизируем
        if original_height <= 720 and (original_bitrate <= 2500 or original_bitrate == 0):
            logger.info("Видео уже оптимизировано, пропускаем сжатие")
            return input_path

        logger.info(f"Оптимизируем видео: {original_height}p -> {target_height}p, битрейт ~{target_bitrate}")

        # Команда для оптимизации
        cmd = [
            "ffmpeg", "-i", input_path,
            "-c:v", "libx264",  # Кодек H.264
            "-preset", "medium",  # Баланс скорость/качество
            "-crf", "23",  # Качество (23 - хороший баланс)
            "-maxrate", max_bitrate,  # Максимальный битрейт
            "-bufsize", buffer_size,  # Размер буфера
            "-vf", f"scale=-2:{target_height}",  # Масштабирование по высоте
            "-c:a", "aac",  # Аудио кодек
            "-b:a", "128k",  # Аудио битрейт
            "-ac", "2",  # Стерео звук
            "-movflags", "+faststart",  # Быстрый старт для стриминга
            "-map_metadata", "-1",  # Удаляем метаданные
            "-y",  # Перезаписать выходной файл
            output_path
        ]

        # Выполняем оптимизацию
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode == 0 and os.path.exists(output_path):
            original_size = os.path.getsize(input_path) / (1024 * 1024)
            optimized_size = os.path.getsize(output_path) / (1024 * 1024)
            compression_ratio = (1 - optimized_size / original_size) * 100

            logger.info(
                f"Оптимизация завершена: {original_size:.1f}MB -> {optimized_size:.1f}MB ({compression_ratio:.1f}% сжатия)")

            # Если оптимизированный файл стал больше, возвращаем оригинал
            if optimized_size >= original_size:
                os.remove(output_path)
                return input_path

            return output_path
        else:
            logger.error(f"Ошибка оптимизации: {result.stderr}")
            return input_path

    except Exception as e:
        logger.error(f"Ошибка при оптимизации видео: {e}")
        return input_path


async def create_thumbnail(video_path: str) -> str:
    """Создает миниатюру для видео"""
    try:
        thumbnail_path = f"{video_path}_thumb.jpg"

        cmd = [
            "ffmpeg", "-i", video_path,
            "-ss", "00:00:05",  # Берем кадр на 5-й секунде
            "-vframes", "1",  # Только один кадр
            "-q:v", "2",  # Качество JPEG
            thumbnail_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0 and os.path.exists(thumbnail_path):
            # Проверяем размер миниатюры (Telegram limit 200KB)
            thumb_size = os.path.getsize(thumbnail_path) / 1024  # KB
            if thumb_size > 190:  # Оставляем запас
                # Уменьшаем качество
                cmd_compress = [
                    "ffmpeg", "-i", thumbnail_path,
                    "-q:v", "5",  # Более сильное сжатие
                    f"{thumbnail_path}_compressed.jpg"
                ]
                subprocess.run(cmd_compress, capture_output=True, timeout=10)
                os.replace(f"{thumbnail_path}_compressed.jpg", thumbnail_path)

            return thumbnail_path

    except Exception as e:
        logger.error(f"Ошибка создания миниатюры: {e}")

    return None


async def handle_send_action(query, context, user_id, file_path):
    """Обработка отправки файла как видео с оптимизацией"""
    try:
        original_size = os.path.getsize(file_path) / (1024 * 1024)  # MB

        # Реальные лимиты Telegram для ботов
        if original_size > 45:
            await handle_large_file(query, context, user_id, file_path, original_size)
            return

        await query.edit_message_text("🔄 Оптимизирую видео для Telegram...")

        # Оптимизируем видео
        optimized_path = await optimize_video_for_telegram(file_path)
        optimized_size = os.path.getsize(optimized_path) / (
                    1024 * 1024) if optimized_path != file_path else original_size

        if optimized_path != file_path:
            await query.edit_message_text(
                f"✅ Видео оптимизировано: {original_size:.1f}MB → {optimized_size:.1f}MB\n🎬 Отправляю...")

        # Создаем миниатюру
        thumbnail_path = await create_thumbnail(optimized_path)

        try:
            # Пытаемся отправить как видео
            with open(optimized_path, 'rb') as video_file:
                await context.bot.send_video(
                    chat_id=user_id,
                    video=video_file,
                    caption=f"📹 {os.path.basename(file_path)}\n"
                            f"📊 {optimized_size:.1f} MB{' (оптимизировано)' if optimized_path != file_path else ''}",
                    thumbnail=thumbnail_path,
                    supports_streaming=True,
                    read_timeout=60,
                    write_timeout=60,
                    connect_timeout=60
                )

            success_message = "✅ Видео успешно отправлено!"
            if optimized_path != file_path:
                compression_ratio = (1 - optimized_size / original_size) * 100
                success_message += f"\n💾 Сжатие: {original_size:.1f}MB → {optimized_size:.1f}MB ({compression_ratio:.1f}%)"

            await query.edit_message_text(success_message)

        except Exception as send_error:
            # Если не получилось как видео, пробуем как документ
            error_msg = str(send_error)
            if "413" in error_msg or "Request Entity Too Large" in error_msg or "400" in error_msg:
                await query.edit_message_text("🔄 Видео слишком большое, пробую отправить как файл...")
                await send_as_document(query, context, user_id, optimized_path)
            else:
                raise send_error

        finally:
            # Удаляем временные файлы
            if thumbnail_path and os.path.exists(thumbnail_path):
                os.remove(thumbnail_path)
            if optimized_path != file_path and os.path.exists(optimized_path):
                os.remove(optimized_path)  # Удаляем оптимизированную копию
            if os.path.exists(file_path):
                os.remove(file_path)  # Удаляем оригинал
            if user_id in user_data:
                del user_data[user_id]

    except Exception as e:
        await handle_send_error(query, e, original_size)


async def handle_large_file(query, context, user_id, file_path, file_size):
    """Обработка больших файлов"""
    # Предлагаем варианты для больших файлов
    keyboard = [
        [
            InlineKeyboardButton("🔄 Оптимизировать и отправить", callback_data=f"optimize_{user_id}"),
            InlineKeyboardButton("✂️ Разделить на части", callback_data=f"split_{user_id}")
        ],
        [
            InlineKeyboardButton("📁 Сохранить на сервере", callback_data="action_move"),
            InlineKeyboardButton("📤 Отправить как есть", callback_data="action_send_as_file")
        ],
        [
            InlineKeyboardButton("🗑️ Удалить файл", callback_data="action_delete")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"📹 Видео большое ({file_size:.1f} MB)\n\n"
        f"📊 Лимит Telegram: ~50 MB\n\n"
        f"💡 Варианты:\n"
        f"• 🔄 Оптимизировать (сжать размер)\n"
        f"• ✂️ Разделить на части\n"
        f"• 📁 Сохранить на сервере\n"
        f"• 📤 Отправить как файл\n"
        f"• 🗑️ Удалить",
        reply_markup=reply_markup
    )


async def handle_optimize_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик оптимизации файла"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if user_id not in user_data or 'file_path' not in user_data[user_id]:
        await query.edit_message_text("❌ Файл не найден или сессия устарела.")
        return

    file_path = user_data[user_id]['file_path']
    original_size = os.path.getsize(file_path) / (1024 * 1024)

    await query.edit_message_text(
        f"🔄 Оптимизирую видео...\n\n"
        f"📹 Исходный размер: {original_size:.1f} MB\n"
        f"⏳ Это может занять несколько минут..."
    )

    try:
        # Оптимизируем видео
        optimized_path = await optimize_video_for_telegram(file_path)
        optimized_size = os.path.getsize(optimized_path) / (1024 * 1024)

        if optimized_path == file_path:
            await query.edit_message_text(
                "ℹ️ Видео уже оптимального размера\n"
                "Пробую отправить как есть..."
            )
            await handle_send_action(query, context, user_id, file_path)
            return

        compression_ratio = (1 - optimized_size / original_size) * 100

        # Обновляем путь к файлу в данных пользователя
        user_data[user_id]['file_path'] = optimized_path

        if optimized_size <= 45:
            await query.edit_message_text(
                f"✅ Оптимизация завершена!\n"
                f"💾 Сжатие: {original_size:.1f}MB → {optimized_size:.1f}MB ({compression_ratio:.1f}%)\n"
                f"🎬 Отправляю видео..."
            )
            await handle_send_action(query, context, user_id, optimized_path)
        else:
            await query.edit_message_text(
                f"✅ Оптимизация завершена!\n"
                f"💾 Сжатие: {original_size:.1f}MB → {optimized_size:.1f}MB ({compression_ratio:.1f}%)\n"
                f"📊 Размер все еще большой: {optimized_size:.1f}MB\n\n"
                f"💡 Выберите действие:"
            )
            # Показываем обновленные варианты
            await handle_large_file(query, context, user_id, optimized_path, optimized_size)

    except Exception as e:
        await query.edit_message_text(
            f"❌ Ошибка при оптимизации:\n`{str(e)}`\n\n"
            f"💡 Попробуйте другой способ.",
            parse_mode='Markdown'
        )


async def send_as_document(query, context, user_id, file_path):
    """Отправляет файл как документ"""
    try:
        file_size = os.path.getsize(file_path) / (1024 * 1024)

        with open(file_path, 'rb') as file:
            await context.bot.send_document(
                chat_id=user_id,
                document=file,
                filename=os.path.basename(file_path),
                caption=f"📁 {os.path.basename(file_path)}\n📊 {file_size:.1f} MB"
            )

        await query.edit_message_text("✅ Файл успешно отправлен!")

        # Удаляем файл после успешной отправки
        try:
            os.remove(file_path)
            if user_id in user_data:
                del user_data[user_id]
        except:
            pass

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка отправки как файл: {str(e)}")


async def handle_send_as_file_action(query, context, user_id, file_path):
    """Обработка отправки как файла"""
    await query.edit_message_text("📤 Отправляю как файл...")
    await send_as_document(query, context, user_id, file_path)


async def handle_send_error(query, error, file_size):
    """Обработка ошибок отправки"""
    error_msg = str(error)
    if "413" in error_msg or "Request Entity Too Large" in error_msg:
        await query.edit_message_text(
            f"❌ Видео слишком большое для Telegram ({file_size:.1f} MB)\n\n"
            f"💡 Пожалуйста, разделите видео на части или сохраните на сервере."
        )
    elif "timed out" in error_msg.lower():
        await query.edit_message_text(
            "❌ Таймаут отправки\n\n"
            "💡 Видео слишком большое для отправки. Попробуйте разделить на части."
        )
    else:
        await query.edit_message_text(f"❌ Ошибка отправки: {str(error)}")


async def split_large_file(file_path: str, max_size_mb: int = 45):
    """Разделяет большой файл на части используя ffmpeg"""
    try:
        file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB
        if file_size <= max_size_mb:
            return [file_path]  # Не нужно разделять

        # Создаем папку для частей
        base_name = os.path.splitext(file_path)[0]
        parts_dir = f"{base_name}_parts"
        os.makedirs(parts_dir, exist_ok=True)

        # Получаем длительность видео
        duration_cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]

        result = subprocess.run(duration_cmd, capture_output=True, text=True)
        total_duration = float(result.stdout.strip())

        # Вычисляем количество частей
        num_parts = max(2, math.ceil(file_size / max_size_mb))  # Минимум 2 части
        part_duration = total_duration / num_parts

        part_files = []

        for i in range(num_parts):
            part_file = f"{parts_dir}/part_{i + 1:02d}.mp4"
            start_time = i * part_duration

            # Сначала пробуем скопировать без перекодирования
            cmd = [
                "ffmpeg", "-i", file_path,
                "-ss", str(start_time),
                "-t", str(part_duration),
                "-c", "copy",  # Копируем без перекодирования
                "-avoid_negative_ts", "make_zero",
                part_file
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode == 0 and os.path.exists(part_file):
                part_size = os.path.getsize(part_file) / (1024 * 1024)

                # Если часть все еще слишком большая, уменьшаем качество
                if part_size > max_size_mb:
                    os.remove(part_file)
                    cmd = [
                        "ffmpeg", "-i", file_path,
                        "-ss", str(start_time),
                        "-t", str(part_duration),
                        "-vf", "scale=854:480",  # Уменьшаем разрешение до 480p
                        "-c:v", "libx264", "-crf", "28",  # Более сильное сжатие
                        "-c:a", "aac", "-b:a", "96k",
                        "-preset", "fast",
                        part_file
                    ]
                    subprocess.run(cmd, capture_output=True, text=True, timeout=300)

                # Проверяем финальный размер
                if os.path.exists(part_file):
                    final_size = os.path.getsize(part_file) / (1024 * 1024)
                    if final_size <= max_size_mb:
                        part_files.append(part_file)
                    else:
                        os.remove(part_file)

        return part_files if part_files else [file_path]

    except Exception as e:
        logger.error(f"Ошибка разделения файла: {e}")
        return [file_path]


async def send_video_parts(chat_id, part_files, context, original_filename):
    """Отправляет части видео в Telegram"""
    total_parts = len(part_files)

    for i, part_file in enumerate(part_files):
        try:
            part_size = os.path.getsize(part_file) / (1024 * 1024)
            part_name = f"{original_filename}.part{i + 1:02d}.mp4"

            # Создаем миниатюру для части
            thumbnail_path = await create_thumbnail(part_file)

            with open(part_file, 'rb') as video_file:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video_file,
                    caption=f"📦 Часть {i + 1}/{total_parts}\n"
                            f"📹 {original_filename}\n"
                            f"📊 {part_size:.1f} MB",
                    thumbnail=thumbnail_path,
                    supports_streaming=True,
                    read_timeout=60,
                    write_timeout=60,
                    connect_timeout=60
                )

            # Удаляем временные файлы
            if thumbnail_path and os.path.exists(thumbnail_path):
                os.remove(thumbnail_path)
            os.remove(part_file)

        except Exception as e:
            logger.error(f"Ошибка отправки части {i + 1}: {e}")
            return False

    return True


async def handle_split_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик разделения файла на части"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if user_id not in user_data or 'file_path' not in user_data[user_id]:
        await query.edit_message_text("❌ Файл не найден или сессия устарела.")
        return

    file_path = user_data[user_id]['file_path']
    original_filename = os.path.basename(file_path)
    file_size = os.path.getsize(file_path) / (1024 * 1024)

    await query.edit_message_text(
        f"✂️ Разделяю видео на части...\n\n"
        f"📹 Видео: {original_filename}\n"
        f"📊 Размер: {file_size:.1f} MB\n"
        f"⏳ Это может занять несколько минут..."
    )

    try:
        # Разделяем файл на части
        part_files = await split_large_file(file_path, max_size_mb=45)

        if len(part_files) <= 1:
            await query.edit_message_text(
                "❌ Не удалось разделить видео\n\n"
                "💡 Попробуйте:\n"
                "• Сохранить видео на сервере\n"
                "• Использовать другое качество при скачивании"
            )
            return

        # Отправляем части как видео
        await query.edit_message_text(
            f"📤 Отправляю {len(part_files)} частей...\n"
            f"⏳ Пожалуйста, подождите..."
        )

        success = await send_video_parts(user_id, part_files, context, original_filename)

        if success:
            # Удаляем исходный файл
            if os.path.exists(file_path):
                os.remove(file_path)

            # Удаляем папку с частями если она пустая
            parts_dir = f"{os.path.splitext(file_path)[0]}_parts"
            if os.path.exists(parts_dir) and not os.listdir(parts_dir):
                os.rmdir(parts_dir)

            await query.edit_message_text(
                f"✅ Все части успешно отправлены!\n\n"
                f"📦 Отправлено частей: {len(part_files)}\n"
                f"🎬 Все части отправлены как видео\n"
                f"💡 Можно смотреть прямо в Telegram!"
            )
        else:
            await query.edit_message_text(
                "❌ Произошла ошибка при отправке частей\n\n"
                "💡 Часть видео могла быть отправлена.\n"
                "Проверьте полученные сообщения."
            )

        # Очищаем данные пользователя
        if user_id in user_data:
            del user_data[user_id]

    except Exception as e:
        await query.edit_message_text(
            f"❌ Ошибка при разделении видео:\n`{str(e)}`\n\n"
            f"💡 Попробуйте сохранить видео на сервере.",
            parse_mode='Markdown'
        )


async def handle_move_action(query, context, user_id, file_path):
    """Обработка перемещения файла в папку пользователя"""
    try:
        # Создаем папку пользователя
        username = user_data[user_id].get('username', f'user_{user_id}')
        user_saved_dir = f"/home/torrent/download/youtube/{username}"
        os.makedirs(user_saved_dir, exist_ok=True)

        # Перемещаем файл в папку пользователя
        new_path = os.path.join(user_saved_dir, os.path.basename(file_path))

        # Если файл с таким именем уже существует, добавляем суффикс
        counter = 1
        original_new_path = new_path
        while os.path.exists(new_path):
            name, ext = os.path.splitext(original_new_path)
            new_path = f"{name}_{counter}{ext}"
            counter += 1

        shutil.move(file_path, new_path)
        file_size = os.path.getsize(new_path) / (1024 * 1024)

        await query.edit_message_text(
            f"✅ Видео сохранено в вашей папке!\n\n"
            f"👤 Пользователь: {username}\n"
            f"📹 Файл: `{os.path.basename(new_path)}`\n"
            f"📊 Размер: {file_size:.1f} MB\n"
            f"📂 Путь: `{new_path}`",
            parse_mode='Markdown'
        )
        # Обновляем путь в данных пользователя
        user_data[user_id]['file_path'] = new_path

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка сохранения: {str(e)}")


async def handle_delete_action(query, context, user_id, file_path):
    """Обработка удаления файла"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            await query.edit_message_text("✅ Видео удалено с сервера")
        else:
            await query.edit_message_text("✅ Файл уже удален")

        # Очищаем данные пользователя
        if user_id in user_data:
            del user_data[user_id]
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка удаления: {str(e)}")


async def handle_post_download_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик действий после скачивания"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    # Обработка специальных действий
    if query.data.startswith('split_'):
        await handle_split_action(update, context)
        return
    elif query.data.startswith('optimize_'):
        await handle_optimize_action(update, context)
        return

    action = query.data.replace('action_', '')

    if user_id not in user_data or 'file_path' not in user_data[user_id]:
        await query.edit_message_text("❌ Файл не найден или сессия устарела.")
        return

    file_path = user_data[user_id]['file_path']

    if action == 'send':
        await handle_send_action(query, context, user_id, file_path)
    elif action == 'send_as_file':
        await handle_send_as_file_action(query, context, user_id, file_path)
    elif action == 'move':
        await handle_move_action(query, context, user_id, file_path)
    elif action == 'delete':
        await handle_delete_action(query, context, user_id, file_path)


async def schedule_file_deletion(user_id: int, file_path: str, delay: int):
    """Планирует автоматическое удаление файла"""
    await asyncio.sleep(delay)

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Автоматически удален файл: {file_path}")

            # Уведомляем пользователя если сессия активна
            if user_id in user_data and user_data[user_id].get('file_path') == file_path:
                try:
                    await Application.builder().token(BOT_TOKEN).build().bot.send_message(
                        chat_id=user_id,
                        text="🗑️ Файл автоматически удален с сервера (через 1 час)"
                    )
                except:
                    pass
    except Exception as e:
        logger.error(f"Ошибка автоматического удаления: {e}")


async def cleanup_old_files():
    """Очистка старых файлов при запуске"""
    download_base = "/home/taras/video_downloads"
    if os.path.exists(download_base):
        for user_dir in os.listdir(download_base):
            user_path = os.path.join(download_base, user_dir)
            if os.path.isdir(user_path):
                try:
                    # Удаляем папки старше 2 часов
                    if os.path.getctime(user_path) < (time.time() - 7200):
                        shutil.rmtree(user_path)
                        logger.info(f"Удалена старая папка: {user_path}")
                except Exception as e:
                    logger.error(f"Ошибка очистки {user_path}: {e}")

    # Также очищаем старые части файлов в saved_videos
    saved_base = "/home/torrent/download/youtube"
    if os.path.exists(saved_base):
        for item in os.listdir(saved_base):
            item_path = os.path.join(saved_base, item)
            try:
                # Удаляем файлы/папки старше 7 дней
                if os.path.getctime(item_path) < (time.time() - 604800):
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
                    logger.info(f"Удален старый файл: {item_path}")
            except Exception as e:
                logger.error(f"Ошибка очистки {item_path}: {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}")


def main():
    """Запуск бота"""
    # Создаем базовые папки
    os.makedirs("/home/taras/video_downloads", exist_ok=True)
    os.makedirs("/home/torrent/download/youtube", exist_ok=True)

    # Проверяем наличие ffmpeg и ffprobe
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
        subprocess.run(["ffprobe", "-version"], capture_output=True)
        logger.info("FFmpeg и FFprobe доступны")
    except:
        logger.warning(
            "FFmpeg или FFprobe не установлены. Установите их для работы с большими файлами: sudo apt install ffmpeg")

    # Очищаем старые файлы
    asyncio.run(cleanup_old_files())

    try:
        application = Application.builder().token(BOT_TOKEN).build()

        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("platforms", platforms_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_video_url))
        application.add_handler(CallbackQueryHandler(handle_quality_selection, pattern="^quality_"))
        application.add_handler(
            CallbackQueryHandler(handle_post_download_actions, pattern="^(action_|split_|optimize_)"))

        application.add_error_handler(error_handler)

        logger.info("Бот запускается...")
        application.run_polling()

    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")


if __name__ == "__main__":
    main()
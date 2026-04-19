#!/usr/bin/env python3
"""
Telegram-бот для скачивания видео с VK Video и Rutube.

Использует python-telegram-bot (Bot API) с поддержкой SOCKS5 / SOCKS4 /
HTTP / HTTPS прокси. MTProto прокси Bot API не поддерживает принципиально
(это ограничение Telegram, а не библиотеки).

Ключевые исправления относительно исходной версии:
  1. Прокси передаётся правильно — через HTTPXRequest(proxy=...), а не
     через os.environ['HTTP_PROXY'] (тот хак в async httpx не работал).
  2. Очистка старых файлов вынесена в post_init — больше не ломает event loop
     (раньше asyncio.run(cleanup_old_files()) закрывал loop перед стартом).
  3. schedule_file_deletion использует существующее Application вместо того,
     чтобы создавать новое на каждый файл (утечка + потенциальные ошибки).
  4. subprocess.run обёрнут в asyncio.to_thread — долгие вызовы yt-dlp/ffmpeg
     больше не блокируют event loop, бот остаётся отзывчивым.
  5. Таймауты httpx увеличены — через прокси дефолтных не хватает.
  6. Прокси пробрасывается и в yt-dlp — раньше он ходил напрямую, мимо прокси.
"""

import os
import sys
import logging
import subprocess
import shutil
import asyncio
import time
import math
import json
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)
from telegram.request import HTTPXRequest

from dotenv import load_dotenv


# ============================================================================
# Конфигурация
# ============================================================================
load_dotenv()

BOT_TOKEN = os.getenv("TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

if not BOT_TOKEN:
    sys.exit("В .env не задан TOKEN")

# ---------- ПРОКСИ ----------
# PROXY_TYPE: socks5 | socks4 | http | https. По умолчанию socks5.
# Если PROXY_HOST пустой — прокси не используется.
PROXY_TYPE = os.getenv("PROXY_TYPE", "socks5").strip().lower()
PROXY_HOST = os.getenv("PROXY_HOST", "").strip()
PROXY_PORT_RAW = os.getenv("PROXY_PORT", "").strip()
PROXY_USER = os.getenv("PROXY_USER", "").strip()
PROXY_PASS = os.getenv("PROXY_PASS", "").strip()

PROXY_URL: Optional[str] = None
if PROXY_HOST and PROXY_PORT_RAW:
    try:
        _proxy_port = int(PROXY_PORT_RAW)
    except ValueError:
        sys.exit(f"PROXY_PORT должен быть числом, получено: {PROXY_PORT_RAW!r}")

    if PROXY_TYPE not in ("socks5", "socks4", "http", "https"):
        sys.exit(
            f"PROXY_TYPE должен быть одним из: socks5, socks4, http, https. "
            f"Получено: {PROXY_TYPE!r}"
        )

    if PROXY_USER and PROXY_PASS:
        PROXY_URL = f"{PROXY_TYPE}://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{_proxy_port}"
    else:
        PROXY_URL = f"{PROXY_TYPE}://{PROXY_HOST}:{_proxy_port}"


# ============================================================================
# Логирование
# ============================================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

if PROXY_URL:
    safe_url = PROXY_URL.replace(PROXY_PASS, "***") if PROXY_PASS else PROXY_URL
    logger.info("Прокси включён: %s", safe_url)
else:
    logger.info("Прокси не используется (прямое подключение)")


# ============================================================================
# Константы и состояние
# ============================================================================
user_data: dict = {}

SUPPORTED_PLATFORMS = {
    "vk.com": "VK Video",
    "vkvideo.ru": "VK Video",
    "rutube.ru": "Rutube",
}

DOWNLOAD_BASE = "/home/taras/video_downloads"
SAVED_BASE = "/home/torrent/download/youtube"
TELEGRAM_SIZE_LIMIT_MB = 45  # Запас от реального ~50MB у Bot API


# ============================================================================
# Команды
# ============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    platforms_list = "\n".join(f"• {name}" for name in SUPPORTED_PLATFORMS.values())
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
    platforms_list = "\n".join(
        f"• {name}" for name in sorted(set(SUPPORTED_PLATFORMS.values()))
    )
    await update.message.reply_text(
        f"📹 Поддерживаемые платформы:\n\n{platforms_list}\n\n"
        f"🔗 Просто отправьте ссылку с любой из этих платформ!\n\n"
        f"⚠️ YouTube временно не поддерживается"
    )


# ============================================================================
# Утилиты
# ============================================================================
def is_supported_platform(url: str) -> bool:
    return any(domain in url for domain in SUPPORTED_PLATFORMS)


def get_platform_name(url: str) -> str:
    for domain, name in SUPPORTED_PLATFORMS.items():
        if domain in url:
            return name
    return "Неизвестная платформа"


def normalize_url(url: str) -> str:
    if "vkvideo.ru" in url:
        url = url.replace("vkvideo.ru", "vk.com")
    if "x.com" in url:
        url = url.replace("x.com", "twitter.com")
    return url


async def run_subprocess(cmd: list, timeout: int = 600) -> subprocess.CompletedProcess:
    """Не блокирует event loop — критично для долгих yt-dlp/ffmpeg."""
    return await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, text=True, timeout=timeout
    )


# ============================================================================
# Обработка ссылок
# ============================================================================
async def handle_video_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user_id = update.message.from_user.id
    username = update.message.from_user.username or f"user_{user_id}"

    logger.info("User %s (%s) requested: %s", user_id, username, url)

    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("❌ Это не похоже на ссылку. Отправьте корректный URL.")
        return

    if not is_supported_platform(url):
        await update.message.reply_text(
            "❌ Эта платформа не поддерживается\n\n"
            "📹 Используйте команду /platforms чтобы увидеть список поддерживаемых платформ.\n"
        )
        return

    url = normalize_url(url)

    user_data[user_id] = {
        "url": url,
        "step": "quality_selection",
        "platform": get_platform_name(url),
        "username": username,
    }

    keyboard = [
        [
            InlineKeyboardButton("🎥 Лучшее качество", callback_data="quality_best"),
            InlineKeyboardButton("⚖️ Сбалансированное", callback_data="quality_720"),
        ],
        [
            InlineKeyboardButton("📱 Для телефона (480p)", callback_data="quality_480"),
            InlineKeyboardButton("🎵 Только аудио", callback_data="quality_audio"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"🎬 Ссылка с {get_platform_name(url)} принята!\nВыберите качество видео:",
        reply_markup=reply_markup,
    )


async def handle_quality_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    quality = query.data.replace("quality_", "")

    if user_id not in user_data:
        await query.edit_message_text("❌ Сессия устарела. Отправьте ссылку заново.")
        return

    url = user_data[user_id]["url"]
    platform = user_data[user_id]["platform"]
    user_data[user_id]["quality"] = quality
    user_data[user_id]["step"] = "downloading"

    quality_names = {
        "best": "лучшее качество",
        "720": "720p (HD)",
        "480": "480p",
        "audio": "только аудио",
    }

    await query.edit_message_text(
        f"⏬ Начинаю скачивание с {platform}...\n"
        f"💾 Качество: {quality_names.get(quality, quality)}\n"
        f"⏳ Это может занять несколько минут..."
    )

    asyncio.create_task(download_video(user_id, url, quality, context))


# ============================================================================
# Скачивание
# ============================================================================
async def download_video(user_id: int, url: str, quality: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_dir = f"{DOWNLOAD_BASE}/user_{user_id}"
        os.makedirs(user_dir, exist_ok=True)

        quality_presets = {
            "best":  {"format": "res:1080,fps", "description": "Лучшее качество (до 1080p)"},
            "720":   {"format": "res:720,fps",  "description": "HD качество (720p)"},
            "480":   {"format": "res:480,fps",  "description": "Стандартное качество (480p)"},
            "audio": {
                "format": "bestaudio/best",
                "description": "Только аудио",
                "audio_params": ["-x", "--audio-format", "mp3", "--audio-quality", "5"],
            },
        }

        preset = quality_presets.get(quality, quality_presets["best"])

        cmd = [
            "yt-dlp",
            "-o", f"{user_dir}/%(title)s.%(ext)s",
            "-S", preset["format"],
            "-f", "mp4",
            "--no-warnings",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "--retries", "3",
            "--fragment-retries", "3",
            "--socket-timeout", "30",
        ]

        # Прокси и для yt-dlp тоже — иначе он будет ходить напрямую
        if PROXY_URL:
            cmd.extend(["--proxy", PROXY_URL])

        if quality == "audio" and "audio_params" in preset:
            cmd.extend(preset["audio_params"])

        cmd.append(url)

        logger.info("yt-dlp: %s", " ".join(cmd))
        result = await run_subprocess(cmd, timeout=600)

        if result.returncode == 0:
            files = os.listdir(user_dir)
            if files:
                latest_file = max(
                    (os.path.join(user_dir, f) for f in files),
                    key=os.path.getctime,
                )

                user_data[user_id]["file_path"] = latest_file
                user_data[user_id]["step"] = "downloaded"

                file_size = os.path.getsize(latest_file) / (1024 * 1024)
                file_name = os.path.basename(latest_file)

                keyboard = [
                    [
                        InlineKeyboardButton("🎬 Отправить видео", callback_data="action_send"),
                        InlineKeyboardButton("📁 Сохранить на сервере", callback_data="action_move"),
                    ],
                    [InlineKeyboardButton("🗑️ Удалить файл", callback_data="action_delete")],
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
                    parse_mode="Markdown",
                )

                asyncio.create_task(
                    schedule_file_deletion(user_id, latest_file, 3600, context.application)
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id, text="❌ Файл не найден после скачивания"
                )
        else:
            error_msg = result.stderr or "Неизвестная ошибка"
            error_message = parse_error_message(error_msg, user_data[user_id]["platform"])
            await context.bot.send_message(chat_id=user_id, text=error_message)

    except Exception as e:
        logger.exception("Ошибка при скачивании")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Неожиданная ошибка:\n`{str(e)}`\n\n"
                 "Попробуйте другую ссылку или повторите позже.",
            parse_mode="Markdown",
        )


def parse_error_message(error: str, platform: str) -> str:
    error_lower = error.lower()
    if "private" in error_lower or "login" in error_lower:
        return f"❌ Видео с {platform} приватное или требует авторизации\n\nДля скачивания нужен доступ к аккаунту."
    if "geo" in error_lower or "region" in error_lower or "country" in error_lower:
        return f"❌ Видео с {platform} недоступно в вашем регионе\n\nИспользуйте VPN или попробуйте другое видео."
    if "removed" in error_lower or "deleted" in error_lower or "unavailable" in error_lower:
        return f"❌ Видео с {platform} было удалено или недоступно"
    if "too large" in error_lower or "size" in error_lower:
        return "❌ Файл слишком большой для скачивания\n\nПопробуйте выбрать меньшее качество."
    return f"❌ Ошибка скачивания с {platform}:\n`{error[:500]}`\n\nПопробуйте другую ссылку."


# ============================================================================
# Оптимизация
# ============================================================================
async def optimize_video_for_telegram(input_path: str, output_path: str = None) -> str:
    if output_path is None:
        output_path = f"{input_path}_optimized.mp4"

    try:
        probe_cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", input_path,
        ]
        result = await run_subprocess(probe_cmd, timeout=60)
        video_info = json.loads(result.stdout)

        video_stream = next(
            (s for s in video_info["streams"] if s["codec_type"] == "video"), None
        )
        if not video_stream:
            return input_path

        original_height = int(video_stream.get("height", 1080))
        original_bitrate = int(video_info["format"].get("bit_rate", 0)) / 1000

        target_height = min(720, original_height)
        max_bitrate = "2500k"
        buffer_size = "5000k"

        if original_height <= 720 and (original_bitrate <= 2500 or original_bitrate == 0):
            logger.info("Видео уже оптимизировано")
            return input_path

        logger.info("Оптимизируем: %sp → %sp", original_height, target_height)

        cmd = [
            "ffmpeg", "-i", input_path,
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-maxrate", max_bitrate, "-bufsize", buffer_size,
            "-vf", f"scale=-2:{target_height}",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-movflags", "+faststart",
            "-map_metadata", "-1",
            "-y", output_path,
        ]
        result = await run_subprocess(cmd, timeout=600)

        if result.returncode == 0 and os.path.exists(output_path):
            original_size = os.path.getsize(input_path) / (1024 * 1024)
            optimized_size = os.path.getsize(output_path) / (1024 * 1024)
            ratio = (1 - optimized_size / original_size) * 100
            logger.info(
                "Оптимизация: %.1fMB → %.1fMB (%.1f%%)",
                original_size, optimized_size, ratio,
            )
            if optimized_size >= original_size:
                os.remove(output_path)
                return input_path
            return output_path

        logger.error("Ошибка ffmpeg: %s", result.stderr)
        return input_path

    except Exception as e:
        logger.exception("Ошибка оптимизации: %s", e)
        return input_path


async def create_thumbnail(video_path: str) -> Optional[str]:
    try:
        thumbnail_path = f"{video_path}_thumb.jpg"
        cmd = [
            "ffmpeg", "-i", video_path,
            "-ss", "00:00:05", "-vframes", "1", "-q:v", "2",
            thumbnail_path,
        ]
        result = await run_subprocess(cmd, timeout=30)

        if result.returncode == 0 and os.path.exists(thumbnail_path):
            thumb_size = os.path.getsize(thumbnail_path) / 1024
            if thumb_size > 190:
                compressed = f"{thumbnail_path}_compressed.jpg"
                await run_subprocess(
                    ["ffmpeg", "-i", thumbnail_path, "-q:v", "5", compressed], timeout=10
                )
                if os.path.exists(compressed):
                    os.replace(compressed, thumbnail_path)
            return thumbnail_path
    except Exception as e:
        logger.error("Ошибка миниатюры: %s", e)
    return None


# ============================================================================
# Отправка
# ============================================================================
async def handle_send_action(query, context, user_id, file_path):
    original_size = 0
    try:
        original_size = os.path.getsize(file_path) / (1024 * 1024)

        if original_size > TELEGRAM_SIZE_LIMIT_MB:
            await handle_large_file(query, context, user_id, file_path, original_size)
            return

        await query.edit_message_text("🔄 Оптимизирую видео для Telegram...")

        optimized_path = await optimize_video_for_telegram(file_path)
        optimized_size = (
            os.path.getsize(optimized_path) / (1024 * 1024)
            if optimized_path != file_path
            else original_size
        )

        if optimized_path != file_path:
            await query.edit_message_text(
                f"✅ Видео оптимизировано: {original_size:.1f}MB → {optimized_size:.1f}MB\n🎬 Отправляю..."
            )

        thumbnail_path = await create_thumbnail(optimized_path)

        try:
            with open(optimized_path, "rb") as video_file:
                await context.bot.send_video(
                    chat_id=user_id,
                    video=video_file,
                    caption=f"📹 {os.path.basename(file_path)}\n"
                            f"📊 {optimized_size:.1f} MB"
                            f"{' (оптимизировано)' if optimized_path != file_path else ''}",
                    thumbnail=thumbnail_path,
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=60,
                )

            success_message = "✅ Видео успешно отправлено!"
            if optimized_path != file_path:
                ratio = (1 - optimized_size / original_size) * 100
                success_message += (
                    f"\n💾 Сжатие: {original_size:.1f}MB → {optimized_size:.1f}MB ({ratio:.1f}%)"
                )
            await query.edit_message_text(success_message)

        except Exception as send_error:
            error_msg = str(send_error)
            if "413" in error_msg or "Request Entity Too Large" in error_msg or "400" in error_msg:
                await query.edit_message_text("🔄 Видео слишком большое, пробую отправить как файл...")
                await send_as_document(query, context, user_id, optimized_path)
            else:
                raise

        finally:
            if thumbnail_path and os.path.exists(thumbnail_path):
                os.remove(thumbnail_path)
            if optimized_path != file_path and os.path.exists(optimized_path):
                os.remove(optimized_path)
            if os.path.exists(file_path):
                os.remove(file_path)
            user_data.pop(user_id, None)

    except Exception as e:
        await handle_send_error(query, e, original_size)


async def handle_large_file(query, context, user_id, file_path, file_size):
    keyboard = [
        [
            InlineKeyboardButton("🔄 Оптимизировать и отправить", callback_data=f"optimize_{user_id}"),
            InlineKeyboardButton("✂️ Разделить на части", callback_data=f"split_{user_id}"),
        ],
        [
            InlineKeyboardButton("📁 Сохранить на сервере", callback_data="action_move"),
            InlineKeyboardButton("📤 Отправить как есть", callback_data="action_send_as_file"),
        ],
        [InlineKeyboardButton("🗑️ Удалить файл", callback_data="action_delete")],
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
        reply_markup=reply_markup,
    )


async def handle_optimize_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if user_id not in user_data or "file_path" not in user_data[user_id]:
        await query.edit_message_text("❌ Файл не найден или сессия устарела.")
        return

    file_path = user_data[user_id]["file_path"]
    original_size = os.path.getsize(file_path) / (1024 * 1024)

    await query.edit_message_text(
        f"🔄 Оптимизирую видео...\n\n"
        f"📹 Исходный размер: {original_size:.1f} MB\n"
        f"⏳ Это может занять несколько минут..."
    )

    try:
        optimized_path = await optimize_video_for_telegram(file_path)
        optimized_size = os.path.getsize(optimized_path) / (1024 * 1024)

        if optimized_path == file_path:
            await query.edit_message_text("ℹ️ Видео уже оптимального размера\nПробую отправить как есть...")
            await handle_send_action(query, context, user_id, file_path)
            return

        ratio = (1 - optimized_size / original_size) * 100
        user_data[user_id]["file_path"] = optimized_path

        if optimized_size <= TELEGRAM_SIZE_LIMIT_MB:
            await query.edit_message_text(
                f"✅ Оптимизация завершена!\n"
                f"💾 Сжатие: {original_size:.1f}MB → {optimized_size:.1f}MB ({ratio:.1f}%)\n"
                f"🎬 Отправляю видео..."
            )
            await handle_send_action(query, context, user_id, optimized_path)
        else:
            await query.edit_message_text(
                f"✅ Оптимизация завершена!\n"
                f"💾 Сжатие: {original_size:.1f}MB → {optimized_size:.1f}MB ({ratio:.1f}%)\n"
                f"📊 Размер всё ещё большой: {optimized_size:.1f}MB\n\n"
                f"💡 Выберите действие:"
            )
            await handle_large_file(query, context, user_id, optimized_path, optimized_size)

    except Exception as e:
        await query.edit_message_text(
            f"❌ Ошибка при оптимизации:\n`{str(e)}`\n\n💡 Попробуйте другой способ.",
            parse_mode="Markdown",
        )


async def send_as_document(query, context, user_id, file_path):
    try:
        file_size = os.path.getsize(file_path) / (1024 * 1024)
        with open(file_path, "rb") as file:
            await context.bot.send_document(
                chat_id=user_id,
                document=file,
                filename=os.path.basename(file_path),
                caption=f"📁 {os.path.basename(file_path)}\n📊 {file_size:.1f} MB",
                read_timeout=120,
                write_timeout=120,
                connect_timeout=60,
            )
        await query.edit_message_text("✅ Файл успешно отправлен!")

        try:
            os.remove(file_path)
        except OSError:
            pass
        user_data.pop(user_id, None)

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка отправки как файл: {str(e)}")


async def handle_send_as_file_action(query, context, user_id, file_path):
    await query.edit_message_text("📤 Отправляю как файл...")
    await send_as_document(query, context, user_id, file_path)


async def handle_send_error(query, error, file_size):
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


# ============================================================================
# Разделение файлов
# ============================================================================
async def split_large_file(file_path: str, max_size_mb: int = TELEGRAM_SIZE_LIMIT_MB):
    try:
        file_size = os.path.getsize(file_path) / (1024 * 1024)
        if file_size <= max_size_mb:
            return [file_path]

        base_name = os.path.splitext(file_path)[0]
        parts_dir = f"{base_name}_parts"
        os.makedirs(parts_dir, exist_ok=True)

        duration_cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path,
        ]
        result = await run_subprocess(duration_cmd, timeout=60)
        total_duration = float(result.stdout.strip())

        num_parts = max(2, math.ceil(file_size / max_size_mb))
        part_duration = total_duration / num_parts

        part_files = []
        for i in range(num_parts):
            part_file = f"{parts_dir}/part_{i + 1:02d}.mp4"
            start_time = i * part_duration

            cmd = [
                "ffmpeg", "-i", file_path,
                "-ss", str(start_time), "-t", str(part_duration),
                "-c", "copy", "-avoid_negative_ts", "make_zero",
                part_file,
            ]
            result = await run_subprocess(cmd, timeout=300)

            if result.returncode == 0 and os.path.exists(part_file):
                part_size = os.path.getsize(part_file) / (1024 * 1024)
                if part_size > max_size_mb:
                    os.remove(part_file)
                    cmd = [
                        "ffmpeg", "-i", file_path,
                        "-ss", str(start_time), "-t", str(part_duration),
                        "-vf", "scale=854:480",
                        "-c:v", "libx264", "-crf", "28",
                        "-c:a", "aac", "-b:a", "96k",
                        "-preset", "fast",
                        part_file,
                    ]
                    await run_subprocess(cmd, timeout=300)

                if os.path.exists(part_file):
                    final_size = os.path.getsize(part_file) / (1024 * 1024)
                    if final_size <= max_size_mb:
                        part_files.append(part_file)
                    else:
                        os.remove(part_file)

        return part_files if part_files else [file_path]

    except Exception as e:
        logger.exception("Ошибка разделения: %s", e)
        return [file_path]


async def send_video_parts(chat_id, part_files, context, original_filename):
    total_parts = len(part_files)
    for i, part_file in enumerate(part_files):
        try:
            part_size = os.path.getsize(part_file) / (1024 * 1024)
            thumbnail_path = await create_thumbnail(part_file)

            with open(part_file, "rb") as video_file:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video_file,
                    caption=f"📦 Часть {i + 1}/{total_parts}\n"
                            f"📹 {original_filename}\n"
                            f"📊 {part_size:.1f} MB",
                    thumbnail=thumbnail_path,
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=60,
                )

            if thumbnail_path and os.path.exists(thumbnail_path):
                os.remove(thumbnail_path)
            os.remove(part_file)

        except Exception as e:
            logger.error("Ошибка отправки части %s: %s", i + 1, e)
            return False
    return True


async def handle_split_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if user_id not in user_data or "file_path" not in user_data[user_id]:
        await query.edit_message_text("❌ Файл не найден или сессия устарела.")
        return

    file_path = user_data[user_id]["file_path"]
    original_filename = os.path.basename(file_path)
    file_size = os.path.getsize(file_path) / (1024 * 1024)

    await query.edit_message_text(
        f"✂️ Разделяю видео на части...\n\n"
        f"📹 Видео: {original_filename}\n"
        f"📊 Размер: {file_size:.1f} MB\n"
        f"⏳ Это может занять несколько минут..."
    )

    try:
        part_files = await split_large_file(file_path, max_size_mb=TELEGRAM_SIZE_LIMIT_MB)

        if len(part_files) <= 1:
            await query.edit_message_text(
                "❌ Не удалось разделить видео\n\n"
                "💡 Попробуйте:\n"
                "• Сохранить видео на сервере\n"
                "• Использовать другое качество при скачивании"
            )
            return

        await query.edit_message_text(
            f"📤 Отправляю {len(part_files)} частей...\n⏳ Пожалуйста, подождите..."
        )

        success = await send_video_parts(user_id, part_files, context, original_filename)

        if success:
            if os.path.exists(file_path):
                os.remove(file_path)
            parts_dir = f"{os.path.splitext(file_path)[0]}_parts"
            if os.path.exists(parts_dir) and not os.listdir(parts_dir):
                os.rmdir(parts_dir)

            await query.edit_message_text(
                f"✅ Все части успешно отправлены!\n\n"
                f"📦 Отправлено частей: {len(part_files)}\n"
                f"🎬 Можно смотреть прямо в Telegram!"
            )
        else:
            await query.edit_message_text(
                "❌ Произошла ошибка при отправке частей\n\n"
                "💡 Часть видео могла быть отправлена.\n"
                "Проверьте полученные сообщения."
            )

        user_data.pop(user_id, None)

    except Exception as e:
        await query.edit_message_text(
            f"❌ Ошибка при разделении видео:\n`{str(e)}`\n\n"
            f"💡 Попробуйте сохранить видео на сервере.",
            parse_mode="Markdown",
        )


# ============================================================================
# Move / Delete
# ============================================================================
async def handle_move_action(query, context, user_id, file_path):
    try:
        username = user_data[user_id].get("username", f"user_{user_id}")
        user_saved_dir = f"{SAVED_BASE}/{username}"
        os.makedirs(user_saved_dir, exist_ok=True)

        new_path = os.path.join(user_saved_dir, os.path.basename(file_path))
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
            parse_mode="Markdown",
        )
        user_data[user_id]["file_path"] = new_path

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка сохранения: {str(e)}")


async def handle_delete_action(query, context, user_id, file_path):
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            await query.edit_message_text("✅ Видео удалено с сервера")
        else:
            await query.edit_message_text("✅ Файл уже удалён")
        user_data.pop(user_id, None)
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка удаления: {str(e)}")


async def handle_post_download_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data.startswith("split_"):
        await handle_split_action(update, context)
        return
    if query.data.startswith("optimize_"):
        await handle_optimize_action(update, context)
        return

    action = query.data.replace("action_", "")

    if user_id not in user_data or "file_path" not in user_data[user_id]:
        await query.edit_message_text("❌ Файл не найден или сессия устарела.")
        return

    file_path = user_data[user_id]["file_path"]

    if action == "send":
        await handle_send_action(query, context, user_id, file_path)
    elif action == "send_as_file":
        await handle_send_as_file_action(query, context, user_id, file_path)
    elif action == "move":
        await handle_move_action(query, context, user_id, file_path)
    elif action == "delete":
        await handle_delete_action(query, context, user_id, file_path)


# ============================================================================
# Фоновые задачи
# ============================================================================
async def schedule_file_deletion(
    user_id: int, file_path: str, delay: int, application: Application
):
    """Автоудаление через delay секунд. Использует переданное Application — не создаём новое!"""
    await asyncio.sleep(delay)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info("Автоудалён файл: %s", file_path)

            if user_id in user_data and user_data[user_id].get("file_path") == file_path:
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text="🗑️ Файл автоматически удалён с сервера (через 1 час)",
                    )
                except Exception as notify_err:
                    logger.warning("Не удалось уведомить: %s", notify_err)
    except Exception as e:
        logger.error("Ошибка автоудаления: %s", e)


async def cleanup_old_files():
    if os.path.exists(DOWNLOAD_BASE):
        for user_dir in os.listdir(DOWNLOAD_BASE):
            user_path = os.path.join(DOWNLOAD_BASE, user_dir)
            if os.path.isdir(user_path):
                try:
                    if os.path.getctime(user_path) < (time.time() - 7200):
                        shutil.rmtree(user_path)
                        logger.info("Удалена старая папка: %s", user_path)
                except Exception as e:
                    logger.error("Ошибка очистки %s: %s", user_path, e)

    if os.path.exists(SAVED_BASE):
        for item in os.listdir(SAVED_BASE):
            item_path = os.path.join(SAVED_BASE, item)
            try:
                if os.path.getctime(item_path) < (time.time() - 604800):
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
                    logger.info("Удалён старый файл: %s", item_path)
            except Exception as e:
                logger.error("Ошибка очистки %s: %s", item_path, e)


async def on_startup(application: Application):
    """Запускается после старта event loop — чистим старые файлы."""
    await cleanup_old_files()
    logger.info("Бот готов к работе")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Ошибка в хэндлере: %s", context.error, exc_info=context.error)


# ============================================================================
# Точка входа
# ============================================================================
def build_request() -> HTTPXRequest:
    """HTTPXRequest с прокси и увеличенными таймаутами (через прокси нужно больше)."""
    kwargs = {
        "connect_timeout": 30.0,
        "read_timeout": 60.0,
        "write_timeout": 60.0,
        "pool_timeout": 30.0,
    }
    if PROXY_URL:
        kwargs["proxy"] = PROXY_URL
    return HTTPXRequest(**kwargs)


def main():
    os.makedirs(DOWNLOAD_BASE, exist_ok=True)
    os.makedirs(SAVED_BASE, exist_ok=True)

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=False)
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=False)
        logger.info("FFmpeg и FFprobe доступны")
    except FileNotFoundError:
        logger.warning("FFmpeg/FFprobe не найдены. sudo apt install ffmpeg")

    # Два отдельных request — один для long polling, другой для обычных запросов
    # (стандартная практика в ptb, чтобы опрос обновлений не конкурировал с заливкой файлов)
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(build_request())
        .get_updates_request(build_request())
        .post_init(on_startup)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("platforms", platforms_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_video_url))
    application.add_handler(CallbackQueryHandler(handle_quality_selection, pattern="^quality_"))
    application.add_handler(
        CallbackQueryHandler(handle_post_download_actions, pattern="^(action_|split_|optimize_)")
    )
    application.add_error_handler(error_handler)

    logger.info("Бот запускается...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

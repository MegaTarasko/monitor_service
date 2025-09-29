#!/usr/bin/env python3
import os
import logging
import subprocess
import shutil
import asyncio
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

#хранение токенов
from dotenv import load_dotenv

#Загрузка токена
load_dotenv()
token = os.getenv('TOKEN')
admin_id = os.getenv('ADMIN_ID')

# Настройки
BOT_TOKEN = token

# Настройка логирования - ИСПРАВЛЕНА ОПЕЧАТКА
# logging.basicConfig(
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',  # asctime вместо asctasctime
#     level=logging.INFO
# )
logger = logging.getLogger(__name__)

# Временное хранилище для данных пользователей
user_data = {}


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "🤖 Бот для скачивания видео\n\n"
        "Просто отправьте мне ссылку на видео!\n"
        "Я предложу выбрать качество, а после скачивания можно будет отправить файл в Telegram"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    await update.message.reply_text(
        "📖 Как использовать бота:\n\n"
        "1. Отправьте ссылку на видео\n"
        "2. Выберите качество из предложенных\n"
        "3. Дождитесь скачивания\n"
        "4. После скачивания можете:\n"
        "   • 📤 Отправить файл в Telegram\n"
        "   • 📁 Переместить в другую папку\n"
        "   • 🗑️ Удалить файл с сервера\n\n"
        "Файлы автоматически удаляются через 1 час"
    )


async def handle_video_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ссылок на видео"""
    url = update.message.text.strip()
    user_id = update.message.from_user.id

    print(f"User {user_id} requested: {url}")

    # Проверяем, что это ссылка
    if not url.startswith(('http://', 'https://')):
        await update.message.reply_text("❌ Это не похоже на ссылку. Отправьте корректный URL.")
        return

    # Меняем vkvideo.ru -> vk.com
    if 'vkvideo.ru' in url:
        url = url.replace('vkvideo.ru', 'vk.com')

    # Сохраняем URL для пользователя
    user_data[user_id] = {'url': url, 'step': 'quality_selection'}

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

    await update.message.reply_text(
        "🎬 Выберите качество видео:",
        reply_markup=reply_markup
    )


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
    user_data[user_id]['quality'] = quality
    user_data[user_id]['step'] = 'downloading'

    await query.edit_message_text("⏬ Начинаю скачивание...")

    # Запускаем скачивание
    asyncio.create_task(download_video(user_id, url, quality, context))


async def download_video(user_id: int, url: str, quality: str, context: ContextTypes.DEFAULT_TYPE):
    """Скачивание видео в фоновом режиме"""
    try:
        # Создаем папку для пользователя
        user_dir = f"/home/taras/video_downloads/user_{user_id}"
        os.makedirs(user_dir, exist_ok=True)

        # Настройки качества
        quality_settings = {
            'best': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
            '720': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
            '480': 'bestvideo[height<=480]+bestaudio/best[height<=480]',
            'audio': 'bestaudio/best'
        }

        format_selection = quality_settings.get(quality, 'best')

        # Команда для yt-dlp
        cmd = [
            "yt-dlp",
            "-o", f"{user_dir}/%(title)s.%(ext)s",
            "-f", format_selection,
            "--no-warnings",
            url
        ]

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
                        InlineKeyboardButton("📤 Отправить в Telegram", callback_data="action_send"),
                        InlineKeyboardButton("📁 Переместить файл в кинотеатр", callback_data="action_move")
                    ],
                    [
                        InlineKeyboardButton("🗑️ Удалить файл", callback_data="action_delete")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"✅ Видео скачано!\n\n"
                         f"📁 Файл: `{file_name}`\n"
                         f"📊 Размер: {file_size:.2f} MB\n"
                         f"💾 Качество: {quality}\n\n"
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
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ Ошибка скачивания:\n`{error_msg}`",
                parse_mode='Markdown'
            )

    except subprocess.TimeoutExpired:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Таймаут скачивания (10 минут)"
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Ошибка: {str(e)}"
        )


async def handle_post_download_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик действий после скачивания"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    action = query.data.replace('action_', '')

    if user_id not in user_data or 'file_path' not in user_data[user_id]:
        await query.edit_message_text("❌ Файл не найден или сессия устарела.")
        return

    file_path = user_data[user_id]['file_path']

    if action == 'send':
        # Отправка файла в Telegram
        try:
            file_size = os.path.getsize(file_path) / (1024 * 1024 * 1024)  # GB

            if file_size > 2:  # Telegram limit ~2GB
                await query.edit_message_text(
                    f"❌ Файл слишком большой для отправки в Telegram ({file_size:.2f} GB)\n"
                    f"Используйте опцию 'Переместить файл'"
                )
            else:
                await query.edit_message_text("📤 Отправляю файл...")

                with open(file_path, 'rb') as file:
                    await context.bot.send_document(
                        chat_id=user_id,
                        document=file,
                        filename=os.path.basename(file_path)
                    )

                await query.edit_message_text("✅ Файл отправлен!")

        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка отправки: {str(e)}")

    elif action == 'move':
        # Перемещение файла
        move_dir = "/home/taras/saved_videos"
        os.makedirs(move_dir, exist_ok=True)

        new_path = os.path.join(move_dir, os.path.basename(file_path))

        try:
            shutil.move(file_path, new_path)
            await query.edit_message_text(
                f"✅ Файл перемещен в:\n`{new_path}`",
                parse_mode='Markdown'
            )
            # Обновляем путь в данных пользователя
            user_data[user_id]['file_path'] = new_path
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка перемещения: {str(e)}")

    elif action == 'delete':
        # Удаление файла
        try:
            os.remove(file_path)
            await query.edit_message_text("✅ Файл удален с сервера")
            # Очищаем данные пользователя
            if user_id in user_data:
                del user_data[user_id]
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка удаления: {str(e)}")


async def schedule_file_deletion(user_id: int, file_path: str, delay: int):
    """Планирует автоматическое удаление файла"""
    await asyncio.sleep(delay)

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Автоматически удален файл: {file_path}")

            # Уведомляем пользователя если сессия активна
            if user_id in user_data and user_data[user_id].get('file_path') == file_path:
                await Application.builder().token(BOT_TOKEN).build().bot.send_message(
                    chat_id=user_id,
                    text="🗑️ Файл автоматически удален с сервера (через 1 час)"
                )
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
                except Exception as e:
                    logger.error(f"Ошибка очистки {user_path}: {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}")


def main():
    """Запуск бота"""
    # Создаем базовые папки
    os.makedirs("/home/taras/video_downloads", exist_ok=True)
    os.makedirs("/home/taras/saved_videos", exist_ok=True)

    # Очищаем старые файлы
    asyncio.run(cleanup_old_files())

    try:
        application = Application.builder().token(BOT_TOKEN).build()

        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_video_url))
        application.add_handler(CallbackQueryHandler(handle_quality_selection, pattern="^quality_"))
        application.add_handler(CallbackQueryHandler(handle_post_download_actions, pattern="^action_"))

        application.add_error_handler(error_handler)

        logger.info("Бот запускается...")
        application.run_polling()

    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")


if __name__ == "__main__":
    main()
"""Main menu and channel statistics UI."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import CHANNELS, get_file


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Statistik", callback_data="main:stats")],
            [InlineKeyboardButton("🔍 OCR", callback_data="main:ocr")],
        ]
    )


def main_menu_text() -> str:
    return "Main Menu"


def _channel_list_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Nocturne", callback_data="stats:channel:nocturne")],
            [
                InlineKeyboardButton(
                    "Two Tiger To Close",
                    callback_data="stats:channel:twotiger",
                )
            ],
            [
                InlineKeyboardButton(
                    "↩️ Back to Main Menu",
                    callback_data="stats:back:main",
                )
            ],
        ]
    )


def count_lines(path) -> int:
    try:
        with path.open("r", encoding="utf-8") as file:
            return sum(1 for _ in file)
    except (FileNotFoundError, OSError):
        return 0


def _channel_stats_text(channel_key: str) -> str:
    channel = CHANNELS[channel_key]
    chat_id = channel["id"]
    approved = count_lines(get_file(chat_id, "users"))
    declined = count_lines(get_file(chat_id, "declined"))
    total = approved + declined
    return (
        f"📊 {channel['name']}\n\n"
        f"✅ Approved: {approved}\n"
        f"🚫 Declined: {declined}\n"
        f"👥 Total: {total}"
    )


def _channel_stats_markup(channel_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "↩️ Back to Channel List",
                    callback_data="stats:channels",
                )
            ],
            [
                InlineKeyboardButton(
                    "🏠 Back to Main Menu",
                    callback_data="stats:back:main",
                )
            ],
        ]
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message:
        await update.message.reply_text(
            main_menu_text(),
            reply_markup=main_menu_markup(),
        )


async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message:
        await update.message.reply_text(
            "📊 Statistik\n\nPilih channel:",
            reply_markup=_channel_list_markup(),
        )


async def stats_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None:
        return

    await query.answer()
    data = query.data or ""

    if data == "main:stats":
        await query.edit_message_text(
            "📊 Statistik\n\nPilih channel:",
            reply_markup=_channel_list_markup(),
        )
        return

    if data == "stats:channels":
        await query.edit_message_text(
            "📊 Statistik\n\nPilih channel:",
            reply_markup=_channel_list_markup(),
        )
        return

    if data == "stats:back:main":
        await query.edit_message_text(
            main_menu_text(),
            reply_markup=main_menu_markup(),
        )
        return

    prefix = "stats:channel:"
    if data.startswith(prefix):
        channel_key = data.removeprefix(prefix)
        if channel_key in CHANNELS:
            await query.edit_message_text(
                _channel_stats_text(channel_key),
                reply_markup=_channel_stats_markup(channel_key),
            )


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(
        CallbackQueryHandler(stats_button, pattern=r"^(main:stats|stats:)")
    )
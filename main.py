"""Telegram bot entry point."""

from telegram.ext import ApplicationBuilder

from approve import register_handlers as register_approve_handlers
from config import TELEGRAM_BOT_TOKEN
from ocr import register_handlers as register_ocr_handlers
from stats import register_handlers as register_stats_handlers


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN belum diatur.")

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    register_approve_handlers(application)
    register_stats_handlers(application)
    register_ocr_handlers(application)

    print("Bot jalan...")
    application.run_polling()


if __name__ == "__main__":
    main()
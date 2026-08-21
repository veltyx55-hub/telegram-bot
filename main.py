"""Telegram bot entry point."""

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from approve import register_handlers as register_approve_handlers
from config import TELEGRAM_BOT_TOKEN
from stats import register_handlers as register_stats_handlers


async def _lazy_ocr_button(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Import the OCR module only when an OCR callback is actually clicked."""

    from ocr import ocr_button

    await ocr_button(update, context)


async def _lazy_receive_zip(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Import the OCR module only when a document is actually received."""

    from ocr import receive_zip

    await receive_zip(update, context)


def _register_lazy_ocr_handlers(application) -> None:
    # Do not import ocr.py while the bot is starting. This keeps the startup
    # path independent from the OCR runtime and its Paddle dependencies.
    application.add_handler(
        CallbackQueryHandler(
            _lazy_ocr_button,
            pattern=r"^(main:ocr|ocr:)",
        )
    )
    application.add_handler(
        MessageHandler(
            filters.Document.ALL & ~filters.COMMAND,
            _lazy_receive_zip,
        )
    )


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN belum diatur.")

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    register_approve_handlers(application)
    register_stats_handlers(application)
    _register_lazy_ocr_handlers(application)

    print("Bot jalan...")
    application.run_polling()


if __name__ == "__main__":
    main()
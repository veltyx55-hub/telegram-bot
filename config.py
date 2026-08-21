"""Shared configuration for the Telegram bot."""

import os
from pathlib import Path
from typing import Final


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Keep TOKEN as a compatibility alias for code that used the original script.
TOKEN = TELEGRAM_BOT_TOKEN

CHANNELS: Final = {
    "nocturne": {
        "name": "Nocturne",
        "id": -1002272523861,
    },
    "twotiger": {
        "name": "Two Tiger To Close",
        "id": -1001703082318,
    },
}

# Existing deployments store these files in the project directory. BOT_DATA_DIR
# is optional and only provides a way to choose another directory explicitly.
DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "."))


def get_file(chat_id: int, file_type: str) -> Path:
    """Return the same users_<chat_id>.txt/declined_<chat_id>.txt paths as before."""

    return DATA_DIR / f"{file_type}_{chat_id}.txt"
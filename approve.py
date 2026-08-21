"""Join-request approval and decline handling.

The approval rules intentionally match the original bot:
users without a username and users already present in users_<channel>.txt are
declined; every other request is approved and persisted.
"""

from telegram import Update
from telegram.ext import Application, ChatJoinRequestHandler, ContextTypes

from config import get_file


users_cache: dict[int, set[int]] = {}


def load_users(chat_id: int) -> set[int]:
    try:
        with get_file(chat_id, "users").open("r", encoding="utf-8") as file:
            return {int(line.strip()) for line in file if line.strip()}
    except (FileNotFoundError, ValueError, OSError):
        return set()


def save_user(chat_id: int, user_id: int) -> None:
    path = get_file(chat_id, "users")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(f"{user_id}\n")


def save_declined(chat_id: int, user_id: int) -> None:
    path = get_file(chat_id, "declined")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(f"{user_id}\n")


async def auto_filter(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Approve or decline one incoming Telegram join request."""

    if update.chat_join_request is None:
        return

    request = update.chat_join_request
    user = request.from_user
    chat_id = request.chat.id
    user_id = user.id
    username = user.username

    if chat_id not in users_cache:
        users_cache[chat_id] = load_users(chat_id)

    users = users_cache[chat_id]

    if username is None:
        save_declined(chat_id, user_id)
        await context.bot.decline_chat_join_request(chat_id, user_id)
        print(f"{user.full_name} ditolak (no username)")
        return

    if user_id in users:
        save_declined(chat_id, user_id)
        await context.bot.decline_chat_join_request(chat_id, user_id)
        print(f"{user.full_name} ditolak (duplicate)")
        return

    users.add(user_id)
    await context.bot.approve_chat_join_request(chat_id, user_id)
    save_user(chat_id, user_id)
    print(f"{user.full_name} approved")


def register_handlers(application: Application) -> None:
    application.add_handler(ChatJoinRequestHandler(auto_filter))
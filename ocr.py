"""Local PaddleOCR flow for ZIP files containing manga/comic pages."""

from __future__ import annotations

import asyncio
import ctypes
import io
import gc
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import zipfile
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from stats import main_menu_markup, main_menu_text


LANGUAGES = {
    "korean": {
        "label": "🇰🇷 Korea",
        "paddle": "korean",
        "recognition_model": "korean_PP-OCRv5_mobile_rec",
        "display": "Korean",
    },
    "english": {
        "label": "🇬🇧 English",
        "paddle": "en",
        "recognition_model": "en_PP-OCRv5_mobile_rec",
        "display": "English",
    },
    "spanish": {
        "label": "🇪🇸 Spanish",
        "paddle": "es",
        "recognition_model": "latin_PP-OCRv5_mobile_rec",
        "display": "Spanish",
    },
}

SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}

# This is deliberately a small, conservative list. Normal dialogue such as
# "WAIT!" should remain in the output rather than being mistaken for an SFX.
KNOWN_SFX = {
    "BANG",
    "BOOM",
    "CRASH",
    "KABOOM",
    "KRRR",
    "THUD",
    "WHOOSH",
    "WHAM",
    "SMACK",
    "SLAM",
    "CLANG",
    "CLATTER",
    "CLICK",
    "SNAP",
    "SPLASH",
    "SWISH",
    "ZAP",
}

# Keep only one language model alive. On a 500 MB container, retaining Korean,
# English, and Spanish engines after users switch languages can trigger OOM.
_ocr_engine: Any | None = None
_ocr_engine_language: str | None = None
_ocr_lock = threading.RLock()


@dataclass(frozen=True)
class OCRPage:
    path: Path
    archive_name: str
    page_label: str


def _load_openmp_runtime() -> None:
    """Load libgomp explicitly on Nix-based runtimes before Paddle imports it."""

    try:
        library_path = subprocess.check_output(
            ["gcc", "-print-file-name=libgomp.so.1"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if library_path and library_path != "libgomp.so.1":
            ctypes.CDLL(library_path, mode=ctypes.RTLD_GLOBAL)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        # Standard Linux installations usually expose libgomp through the
        # dynamic linker already, so this compatibility step is optional.
        pass


def _language_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LANGUAGES["korean"]["label"],
                    callback_data="ocr:language:korean",
                ),
                InlineKeyboardButton(
                    LANGUAGES["english"]["label"],
                    callback_data="ocr:language:english",
                ),
            ],
            [
                InlineKeyboardButton(
                    LANGUAGES["spanish"]["label"],
                    callback_data="ocr:language:spanish",
                )
            ],
            [InlineKeyboardButton("↩️ Back", callback_data="ocr:back:main")],
        ]
    )


def _language_text() -> str:
    return "🔍 OCR\n\nSelect language:"


def _upload_prompt_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("↩️ Back", callback_data="ocr:back:language")]]
    )


def _upload_prompt(language_key: str) -> str:
    language = LANGUAGES[language_key]["display"]
    return (
        f"🔍 OCR — {language}\n\n"
        "Silakan kirim file ZIP yang berisi halaman manga/komik."
    )


def _output_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 Teks Langsung", callback_data="ocr:output:text")],
            [InlineKeyboardButton("📄 File DOC", callback_data="ocr:output:doc")],
            [InlineKeyboardButton("📃 File TXT", callback_data="ocr:output:txt")],
        ]
    )


def _natural_sort_key(path: Path) -> list[Any]:
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", path.name)
    ]


def _page_label_from_filename(filename: str) -> str:
    stem = PurePosixPath(filename).stem
    match = re.match(r"^\s*(\d+)", stem)
    if match:
        # Keep exactly the digit string from the filename: 01 stays 01.
        return match.group(1)
    return stem.strip() or "unknown"


def _safe_extract_images(
    archive: zipfile.ZipFile,
    destination: Path,
) -> tuple[str, list[OCRPage]]:
    pages: list[OCRPage] = []
    destination_resolved = destination.resolve()
    chapter_title = ""

    for member in archive.infolist():
        if member.is_dir():
            continue
        member_path = PurePosixPath(member.filename)
        if member_path.suffix.casefold() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue

        output_path = (destination / Path(*member_path.parts)).resolve()
        if destination_resolved not in output_path.parents:
            continue
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Stream the member instead of creating a second in-memory copy of a
        # potentially large comic page.
        with archive.open(member) as source, output_path.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)

        # The first folder in the archive is the chapter folder. This works
        # for Chapter 01/01.jpg and also for Chapter 01/pages/01.jpg.
        if not chapter_title and len(member_path.parts) > 1:
            chapter_title = member_path.parts[0]

        pages.append(
            OCRPage(
                path=output_path,
                archive_name=member.filename,
                page_label=_page_label_from_filename(member.filename),
            )
        )

    pages.sort(key=lambda page: _natural_sort_key(Path(page.archive_name)))
    return chapter_title, pages


def _get_ocr_engine(language_key: str) -> Any:
    global _ocr_engine, _ocr_engine_language

    if _ocr_engine is not None and _ocr_engine_language == language_key:
        return _ocr_engine

    _load_openmp_runtime()
    try:
        from paddleocr import PaddleOCR
    except ImportError as error:
        raise RuntimeError(
            "PaddleOCR belum terpasang. Jalankan instalasi dari requirements.txt."
        ) from error

    language = LANGUAGES[language_key]
    paddle_language = language["paddle"]
    try:
        # PaddleOCR 3.x. Explicit mobile models avoid the default medium
        # detector/recognizer (PP-OCRv6_medium_*), which is too large for
        # Railway's ~500 MB limit. The language-specific mobile recognizers
        # preserve Korean, English, and Spanish support.
        engine = PaddleOCR(
            lang=paddle_language,
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name=language["recognition_model"],
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
            device="cpu",
            text_recognition_batch_size=1,
            text_det_limit_side_len=960,
        )
    except (TypeError, ValueError):
        # Compatibility with PaddleOCR 2.x installations.
        engine = PaddleOCR(
            lang=paddle_language,
            use_angle_cls=False,
            use_gpu=False,
        )

    # Release a previous language engine before retaining the new one. The
    # lock in _recognize_page prevents this from happening during inference.
    if _ocr_engine is not None:
        del _ocr_engine
        gc.collect()
    _ocr_engine = engine
    _ocr_engine_language = language_key
    return engine


def _json_value(result: Any) -> Any:
    value = getattr(result, "json", None)
    if callable(value):
        value = value()
    if value is not None:
        return value

    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return result


def _texts_from_paddle_result(result: Any) -> list[str]:
    """Read text from PaddleOCR 2.x and 3.x result shapes."""

    result = _json_value(result)

    if isinstance(result, dict):
        if "res" in result:
            return _texts_from_paddle_result(result["res"])
        texts = result.get("rec_texts")
        if isinstance(texts, list):
            return [str(text).strip() for text in texts if str(text).strip()]

        for value in result.values():
            found = _texts_from_paddle_result(value)
            if found:
                return found
        return []

    if isinstance(result, (list, tuple)):
        # PaddleOCR 2.x: [[[[box], ["text", score]], ...]]
        if (
            len(result) >= 2
            and isinstance(result[0], (list, tuple))
            and isinstance(result[1], (list, tuple))
            and result[1]
            and isinstance(result[1][0], str)
        ):
            text = str(result[1][0]).strip()
            return [text] if text else []

        output: list[str] = []
        for value in result:
            output.extend(_texts_from_paddle_result(value))
        return output

    return []


def _recognize_page(image_path: Path, language_key: str) -> list[str]:
    # Paddle inference is CPU-bound; serializing it also prevents two users
    # from loading/using separate model graphs at the same time.
    with _ocr_lock:
        engine = _get_ocr_engine(language_key)

        if hasattr(engine, "predict"):
            result = engine.predict(str(image_path))
        else:
            result = engine.ocr(str(image_path), cls=False)

    return _texts_from_paddle_result(result)


def _is_likely_sfx(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return True

    normalized = re.sub(r"[\s.!?~…*_+#\-]+$", "", cleaned).upper()
    if normalized in KNOWN_SFX:
        return True

    # A conservative fallback for stylized repeated sounds (e.g. KRRR).
    # It requires uppercase/repeated letters so ordinary prose is retained.
    letters = re.sub(r"[^A-Z]", "", cleaned)
    if letters and cleaned == cleaned.upper() and re.search(r"(.)\1{2,}", letters):
        return True
    return False


def _format_page(page_label: str, texts: list[str]) -> str:
    lines = [
        f"Page {page_label}",
        "",
    ]
    for text in texts:
        cleaned = " ".join(text.split())
        if cleaned and not _is_likely_sfx(cleaned):
            lines.append(f'\"\": {cleaned}')
            lines.append("")
    return "\n".join(lines).rstrip()


def _split_for_telegram(text: str, limit: int = 4096) -> list[str]:
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def _make_docx(raw_text: str) -> io.BytesIO:
    try:
        from docx import Document
    except ImportError as error:
        raise RuntimeError(
            "python-docx belum terpasang. Jalankan instalasi dari requirements.txt."
        ) from error

    document = Document()
    document.add_paragraph(raw_text)
    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    return output


async def _edit_status(message, text: str) -> None:
    try:
        await message.edit_text(text)
    except TelegramError as error:
        print(f"Telegram progress update error: {error}", flush=True)
        traceback.print_exc()
    except Exception as error:
        # Keep unexpected Telegram-message errors isolated from OCR processing.
        print(f"Telegram progress update error: {error}", flush=True)
        traceback.print_exc()


async def _edit_output_buttons(message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=_output_markup())
    except TelegramError as error:
        print(f"Telegram result buttons update error: {error}", flush=True)
        traceback.print_exc()
    except Exception as error:
        print(f"Telegram result buttons update error: {error}", flush=True)
        traceback.print_exc()


async def _safe_send_message(context, chat_id: int, text: str) -> bool:
    """Send Telegram messages without turning Telegram failures into OCR failures."""
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
        return True
    except Exception as error:
        print(f"Telegram send_message error: {error}", flush=True)
        traceback.print_exc()
        return False


async def _safe_send_document(context, chat_id: int, document, filename: str) -> bool:
    """Send Telegram documents without crashing the OCR workflow."""
    try:
        await context.bot.send_document(
            chat_id=chat_id,
            document=document,
            filename=filename,
        )
        return True
    except Exception as error:
        print(f"Telegram send_document error: {error}", flush=True)
        traceback.print_exc()
        return False


async def ocr_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None:
        return

    await query.answer()
    data = query.data or ""

    if data == "main:ocr":
        await query.edit_message_text(_language_text(), reply_markup=_language_markup())
        return

    if data == "ocr:back:main":
        context.user_data.pop("ocr_language", None)
        await query.edit_message_text(
            main_menu_text(),
            reply_markup=main_menu_markup(),
        )
        return

    if data == "ocr:back:language":
        context.user_data.pop("ocr_language", None)
        await query.edit_message_text(_language_text(), reply_markup=_language_markup())
        return

    prefix = "ocr:language:"
    if data.startswith(prefix):
        language_key = data.removeprefix(prefix)
        if language_key in LANGUAGES:
            context.user_data["ocr_language"] = language_key
            await query.edit_message_text(
                _upload_prompt(language_key),
                reply_markup=_upload_prompt_markup(),
            )
        return

    if data.startswith("ocr:output:"):
        raw_text = context.user_data.get("ocr_raw_text")
        if not isinstance(raw_text, str):
            await query.edit_message_text(
                "Hasil OCR sudah tidak tersedia. Silakan mulai lagi dari menu OCR.",
                reply_markup=main_menu_markup(),
            )
            return

        output_type = data.removeprefix("ocr:output:")
        await query.edit_message_text("✅ OCR selesai!\n\nMengirim hasil...")

        if output_type == "text":
            for chunk in _split_for_telegram(raw_text):
                await _safe_send_message(context, query.message.chat_id, chunk)
        elif output_type == "txt":
            await _safe_send_document(
                context,
                query.message.chat_id,
                io.BytesIO(raw_text.encode("utf-8")),
                "RAW.txt",
            )
        elif output_type == "doc":
            try:
                document = await asyncio.to_thread(_make_docx, raw_text)
            except RuntimeError as error:
                await _safe_send_message(
                    context,
                    query.message.chat_id,
                    f"❌ {error}",
                )
                return
            await _safe_send_document(
                context,
                query.message.chat_id,
                document,
                "RAW.docx",
            )


async def receive_zip(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.message.document is None:
        return

    language_key = context.user_data.get("ocr_language")
    if language_key not in LANGUAGES:
        return

    document = update.message.document
    if not (document.file_name or "").casefold().endswith(".zip"):
        await update.message.reply_text("❌ File yang dikirim harus berupa ZIP.")
        return

    status_message = await update.message.reply_text("⏳ OCR sedang berjalan...")
    last_update = 0.0

    try:
        with tempfile.TemporaryDirectory(prefix="telegram-ocr-") as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / "chapter.zip"
            try:
                telegram_file = await context.bot.get_file(document.file_id)
                await telegram_file.download_to_drive(custom_path=str(zip_path))
            except TelegramError as error:
                print(f"Telegram ZIP download error: {error}", flush=True)
                traceback.print_exc()
                await _edit_status(
                    status_message,
                    "❌ Telegram gagal mengunduh ZIP. Silakan kirim ulang ZIP dan coba lagi.",
                )
                return
            except Exception as error:
                print(f"ZIP download error: {error}", flush=True)
                traceback.print_exc()
                await _edit_status(
                    status_message,
                    "❌ ZIP gagal diunduh. Silakan coba kirim ulang.",
                )
                return

            try:
                with zipfile.ZipFile(zip_path) as archive:
                    if archive.testzip() is not None:
                        raise zipfile.BadZipFile("CRC check failed")
                    chapter_title, pages = _safe_extract_images(
                        archive,
                        temp_path / "pages",
                    )
            except (zipfile.BadZipFile, OSError):
                await _edit_status(status_message, "❌ File ZIP tidak valid.")
                return

            if not pages:
                await _edit_status(
                    status_message,
                    "❌ Tidak ditemukan gambar di dalam ZIP.",
                )
                return

            output_pages: list[str] = []
            failed_pages: list[str] = []
            total_pages = len(pages)

            for index, page in enumerate(pages, start=1):
                try:
                    texts = await asyncio.to_thread(
                        _recognize_page,
                        page.path,
                        language_key,
                    )
                    output_pages.append(_format_page(page.page_label, texts))
                except Exception as error:
                    print(
                        f"OCR error Page {page.page_label}: {error}",
                        flush=True,
                    )
                    traceback.print_exc()
                    failed_pages.append(page.page_label)
                    output_pages.append(_format_page(page.page_label, []))

                # Progress is updated once per completed page. The helper
                # isolates Telegram timeouts, so OCR continues if this edit
                # cannot reach Telegram.
                status = (
                    "⏳ OCR sedang berjalan...\n\n"
                    f"Page {index}/{total_pages}"
                )
                await _edit_status(status_message, status)

            if not chapter_title:
                chapter_title = Path(document.file_name or "Chapter").stem
            raw_text = f"{chapter_title}\n\n" + "\n\n".join(output_pages)
            context.user_data["ocr_raw_text"] = raw_text
            failed_note = ""
            if failed_pages:
                failed_note = (
                    "\n\n⚠️ Page "
                    + ", ".join(failed_pages)
                    + " gagal diproses."
                )
            await _edit_status(
                status_message,
                "✅ OCR selesai!"
                + failed_note
                + "\n\nMau dikirim dalam bentuk apa?",
            )
            await _edit_output_buttons(status_message)
    except Exception as error:
        print(f"OCR chapter error: {error}", flush=True)
        traceback.print_exc()
        await _edit_status(
            status_message,
            "❌ Terjadi kesalahan saat memproses OCR. Silakan coba lagi.",
        )


def register_handlers(application: Application) -> None:
    application.add_handler(
        CallbackQueryHandler(
            ocr_button,
            pattern=r"^(main:ocr|ocr:)",
        )
    )
    application.add_handler(
        MessageHandler(
            filters.Document.ALL & ~filters.COMMAND,
            receive_zip,
        )
    )
"""Developer-facing terminal logging for the web layer.

Mirrors the exact step-by-step console style already used by the CLI
prototype in main.py (print_step / print_ui_hint / print_debug_state),
so anyone who has used the CLI sees the same shape of output while
using the web UI — just routed through `python manage.py runserver`
instead of the interactive prompt.

main.py itself is NOT imported or modified here. It keeps working
standalone exactly as before; this module only reproduces its *visual
logging contract* for the Django request/response cycle.

Never logs: API keys, tokens, full image bytes, or raw stack traces
at INFO level. Exceptions are logged with `logger.exception` only at
DEBUG-safe boundaries and never returned to the browser.
"""
import logging
import os
import sys

_CONFIGURED = False


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"
    BLUE = "\033[34m"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() or os.name == "nt"


_USE_COLOR = _supports_color()


def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"{code}{text}{Color.RESET}"


def configure_logging():
    """Call once at Django startup (AppConfig.ready()). Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("django.utils.autoreload").setLevel(logging.WARNING)
    _CONFIGURED = True


logger = logging.getLogger("swiggy_support")


def chat_received(thread_id: str, has_image: bool, message_preview: str):
    tag = _c(Color.CYAN, "[CHAT]")
    logger.info(f"{tag} New message received | thread={thread_id[:8]} | image={has_image}")
    if message_preview:
        preview = message_preview[:80] + ("..." if len(message_preview) > 80 else "")
        logger.info(f"{tag} {_c(Color.DIM, preview)}")


def thread_loading(thread_id: str):
    logger.info(f"{_c(Color.MAGENTA, '[THREAD]')} Loading thread {thread_id[:8]}")


def checkpoint_loaded(thread_id: str, tool_usage: str | None):
    tag = _c(Color.MAGENTA, "[CHECKPOINT]")
    logger.info(f"{tag} Restored checkpoint for {thread_id[:8]} | tool_usage={tool_usage}")


def checkpoint_missing(thread_id: str):
    tag = _c(Color.YELLOW, "[CHECKPOINT]")
    logger.info(f"{tag} No prior checkpoint for {thread_id[:8]} — starting fresh state")


def checkpoint_corrupted(thread_id: str, error: Exception):
    tag = _c(Color.RED, "[CHECKPOINT]")
    logger.warning(f"{tag} Corrupted/unreadable checkpoint for {thread_id[:8]}: {error!r} — resetting")


def checkpoint_deleted(thread_id: str, reason: str):
    tag = _c(Color.MAGENTA, "[CHECKPOINT]")
    logger.info(f"{tag} Deleted checkpoint for {thread_id[:8]} ({reason})")


def image_upload_received(filename: str, size_bytes: int):
    tag = _c(Color.BLUE, "[IMAGE]")
    logger.info(f"{tag} Upload received | {filename} | {size_bytes / 1024:.1f} KB")


def image_validated(ok: bool, reason: str = ""):
    tag = _c(Color.BLUE, "[IMAGE]")
    if ok:
        logger.info(f"{tag} Validation successful")
    else:
        logger.warning(f"{tag} Validation failed: {reason}")


def image_exif_step(found: bool):
    tag = _c(Color.DIM, "[IMAGE]")
    logger.info(f"{tag} EXIF metadata {'found' if found else 'NOT found — AI-generated suspected'}")


def vlm_step(provider_hint: str = "DashScope -> OpenRouter fallback chain"):
    logger.info(f"{_c(Color.DIM, '[AI]')} Running vision validation ({provider_hint})")


def ai_step_start():
    logger.info(f"{_c(Color.GREEN, '[AI]')} Starting response generation")


def ai_step_done(tool_usage: str | None, resolved: bool):
    tag = _c(Color.GREEN, "[AI]")
    logger.info(f"{tag} Response generated | tool_usage={tool_usage} | resolved={resolved}")


def ui_hint(tool_usage: str | None):
    if tool_usage == "ShowInputSelectionToolNode":
        logger.info(f"{_c(Color.BLUE, '[UI]')} Image upload widget shown. Text input disabled.")
    else:
        logger.info(f"{_c(Color.BLUE, '[UI]')} Normal chat input enabled.")


def error(message: str):
    logger.error(f"{_c(Color.RED, '[ERROR]')} {message}")


def warning(message: str):
    logger.warning(f"{_c(Color.YELLOW, '[WARN]')} {message}")

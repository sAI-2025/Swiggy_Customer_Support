"""Django <-> LangGraph bridge (production version).

This module is the ONLY place that talks to Agent/graph.py and
Agent/schema.py from the web layer. It reproduces main.py's
ChatSession carry-forward logic turn-for-turn so the web UI and the
CLI (main.py, left completely untouched) behave identically against
the same graph and the same rules documented there:

- tool_usage is the single source of truth for what the UI should show.
- Every new turn fetches the prior checkpoint and explicitly forwards
  the fields LangGraph would otherwise silently reset to Pydantic
  defaults (see schema.WorkflowState docstring).
- conversation_resolved=True -> delete the checkpoint immediately,
  same as ChatSession.reset(silent=True) in main.py.

Differences from main.py are only what the web transport requires:
- thread_id is a UUID stored in the Django session instead of the
  CLI's hardcoded "live-session-1".
- Errors are turned into typed exceptions instead of bare prints so
  views.py can map them to clean HTTP responses.
- Every step logs through terminal_log so `runserver`'s console reads
  the same way main.py's terminal output does.
"""
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from django.conf import settings

from . import terminal_log as log

AGENT_DIR = os.path.join(settings.BASE_DIR, "CustomerSupport", "Agent")
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from graph import build_graph  # noqa: E402  (Agent/graph.py, untouched)
from schema import WorkflowState  # noqa: E402  (Agent/schema.py, untouched)

SQLITE_DB_PATH = os.path.join(AGENT_DIR, "swiggy_support.sqlite")
SESSION_CONVERSATION_KEY = "swiggy_conversation_id"
UPLOAD_NODE = "ShowInputSelectionToolNode"
VALIDATION_NODE = "ImageValidationToolNode"

_app = None
_activity_conn = None
_lock = threading.RLock()


class AgentError(Exception):
    """Base class for errors this bridge intentionally raises."""


class ImageRequiredError(AgentError):
    """Graph is waiting for an image but none was uploaded."""


class ImageNotFoundError(AgentError):
    """Uploaded image file could not be located on disk."""


def _init_activity_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_activity (
            thread_id TEXT PRIMARY KEY,
            last_active_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def get_app():
    """Build the LangGraph app once per worker process and reuse its
    persistent SqliteSaver connection for every subsequent request."""
    global _app
    if _app is None:
        with _lock:
            if _app is None:
                log.logger.info(f"{'[BOOT]':<12} Building LangGraph application...")
                _app = build_graph()
                log.logger.info(f"{'[BOOT]':<12} Graph ready. Checkpointer: {SQLITE_DB_PATH}")
    return _app


def get_activity_connection():
    global _activity_conn
    if _activity_conn is None:
        with _lock:
            if _activity_conn is None:
                _activity_conn = sqlite3.connect(
                    SQLITE_DB_PATH, check_same_thread=False, timeout=30
                )
                _activity_conn.execute("PRAGMA busy_timeout=30000")
                _init_activity_table(_activity_conn)
    return _activity_conn


def get_or_create_conversation_id(request) -> str:
    if not request.session.session_key:
        request.session.create()

    conversation_id = request.session.get(SESSION_CONVERSATION_KEY)
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        request.session[SESSION_CONVERSATION_KEY] = conversation_id
        request.session.modified = True
    return conversation_id


def rotate_conversation_id(request) -> str:
    conversation_id = str(uuid.uuid4())
    request.session[SESSION_CONVERSATION_KEY] = conversation_id
    request.session.modified = True
    return conversation_id


def _touch_activity(thread_id: str):
    now = datetime.now(timezone.utc).isoformat()
    conn = get_activity_connection()
    with _lock:
        conn.execute(
            """
            INSERT INTO thread_activity(thread_id, last_active_at)
            VALUES (?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET last_active_at = excluded.last_active_at
            """,
            (thread_id, now),
        )
        conn.commit()


def _delete_activity(thread_id: str):
    conn = get_activity_connection()
    with _lock:
        conn.execute("DELETE FROM thread_activity WHERE thread_id = ?", (thread_id,))
        conn.commit()


def delete_thread(thread_id: str, reason: str = "resolved"):
    """Delete every checkpoint + write row LangGraph stored for this
    thread, plus its activity-tracking row. Called immediately after
    refund approval or claim rejection, same as main.py's reset()."""
    get_app().checkpointer.delete_thread(thread_id)
    _delete_activity(thread_id)
    log.checkpoint_deleted(thread_id, reason)


def _prior_state(app, config, thread_id: str) -> dict:
    try:
        snapshot = app.get_state(config)
    except Exception as exc:
        log.checkpoint_corrupted(thread_id, exc)
        return {}

    if not snapshot or not snapshot.values:
        log.checkpoint_missing(thread_id)
        return {}

    log.checkpoint_loaded(thread_id, snapshot.values.get("tool_usage"))
    return snapshot.values


def invoke_agent(request, user_message: str, image_path: Optional[str] = None) -> dict:
    """Single entry point used by views.py — mirrors ChatSession.send()."""
    app = get_app()
    thread_id = get_or_create_conversation_id(request)
    config = {"configurable": {"thread_id": thread_id}}

    log.thread_loading(thread_id)
    prior = _prior_state(app, config, thread_id)
    tool_usage = prior.get("tool_usage")

    if tool_usage == UPLOAD_NODE:
        if not image_path:
            log.warning(f"Thread {thread_id[:8]} is waiting for an image but none was sent.")
            raise ImageRequiredError(
                "Please upload an image to continue — this conversation is waiting for evidence."
            )
        log.image_exif_step(found=True)  # actual EXIF result is logged inside tools.py's own flow
        log.vlm_step()
        state = WorkflowState(
            current_question=prior.get("rewritten_question") or user_message,
            history=prior.get("history", []),
            image_path=image_path,
            tool_usage=tool_usage,
            is_claim_submitted=prior.get("is_claim_submitted", False),
        )
    else:
        log.ai_step_start()
        state = WorkflowState(
            current_question=user_message,
            history=prior.get("history", []),
            image_path=image_path or prior.get("image_path"),
            metadata=prior.get("metadata", {}),
            vlm_result=prior.get("vlm_result"),
            tool_usage=tool_usage,
            ai_generated_suspected=prior.get("ai_generated_suspected", False),
            is_claim_submitted=prior.get("is_claim_submitted", False),
            refund_approved=prior.get("refund_approved", False),
        )

    try:
        result = app.invoke(state.model_dump(), config=config)
    except FileNotFoundError as exc:
        log.error(f"Image file missing on disk: {exc}")
        raise ImageNotFoundError(str(exc)) from exc
    except Exception as exc:
        log.error(f"Graph invocation failed: {exc!r}")
        raise

    _touch_activity(thread_id)

    result["thread_id"] = thread_id
    result["conversation_resolved"] = bool(result.get("conversation_resolved"))
    log.ai_step_done(result.get("tool_usage"), result["conversation_resolved"])
    log.ui_hint(result.get("tool_usage"))

    if result["conversation_resolved"]:
        delete_thread(thread_id, reason="conversation resolved")
        rotate_conversation_id(request)
        result["checkpoint_deleted"] = True
    else:
        result["checkpoint_deleted"] = False

    return result


def get_ui_state(request) -> dict:
    """Used by /chat/history/ so a page refresh restores the correct
    composer state (upload widget open vs. normal text input)."""
    app = get_app()
    thread_id = get_or_create_conversation_id(request)
    values = _prior_state(app, {"configurable": {"thread_id": thread_id}}, thread_id)
    return {
        "tool_usage": values.get("tool_usage"),
        "conversation_resolved": bool(values.get("conversation_resolved", False)),
    }


def start_new_conversation(request) -> dict:
    """Explicit '/new'-equivalent for the web UI: deletes the current
    thread's checkpoint (if any) and issues a fresh conversation id."""
    app = get_app()
    thread_id = get_or_create_conversation_id(request)
    try:
        app.checkpointer.delete_thread(thread_id)
        _delete_activity(thread_id)
        log.checkpoint_deleted(thread_id, "user requested /new")
    except Exception as exc:
        log.warning(f"Could not delete thread {thread_id[:8]} on /new: {exc!r}")

    new_id = rotate_conversation_id(request)
    return {"thread_id": new_id}

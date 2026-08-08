"""Web layer for the Swiggy Support chat widget.

Thin HTTP boundary only — all AI/graph/checkpoint logic lives in
agent_bridge.py (which wraps Agent/graph.py, untouched). This module's
only job is: validate the HTTP request, call the bridge, translate
bridge exceptions into clean JSON errors, and persist the visible
chat transcript (ChatMessage) for the widget's history view.
"""
import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import terminal_log as log
from .agent_bridge import (
    ImageNotFoundError,
    ImageRequiredError,
    get_ui_state,
    invoke_agent,
    start_new_conversation,
)
from .models import ChatMessage

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


def index(request):
    if not request.session.session_key:
        request.session.create()
    return render(request, "index.html")


@require_http_methods(["GET"])
def chat_history(request):
    if not request.session.session_key:
        request.session.create()

    session_key = request.session.session_key
    messages = ChatMessage.objects.filter(session_key=session_key)
    data = [
        {
            "sender": message.sender,
            "message": message.message,
            "image": message.image.url if message.image else None,
            "time": message.created_at.strftime("%I:%M %p"),
        }
        for message in messages
    ]

    ui_state = get_ui_state(request)
    return JsonResponse({
        "messages": data,
        "tool_usage": ui_state["tool_usage"],
        "conversation_resolved": ui_state["conversation_resolved"],
    })


@csrf_exempt
@require_http_methods(["POST"])
def chat_send(request):
    if not request.session.session_key:
        request.session.create()

    image_file = None
    user_message = ""
    is_multipart = bool(
        request.content_type and request.content_type.startswith("multipart/form-data")
    )

    if is_multipart:
        user_message = (request.POST.get("message") or "").strip()
        image_file = request.FILES.get("image")

        if image_file:
            log.image_upload_received(image_file.name, image_file.size)

            if image_file.content_type not in ALLOWED_IMAGE_TYPES:
                log.warning(f"Rejected upload: unsupported type {image_file.content_type}")
                return JsonResponse(
                    {"error": "Unsupported image format. Use JPG, PNG, WEBP or GIF."},
                    status=400,
                )
            if image_file.size > MAX_IMAGE_SIZE:
                log.warning(f"Rejected upload: {image_file.size} bytes exceeds 5 MB limit")
                return JsonResponse(
                    {"error": "Image too large. Maximum size is 5 MB."},
                    status=400,
                )
    else:
        try:
            body = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("Rejected request: invalid JSON body")
            return JsonResponse({"error": "Invalid JSON request."}, status=400)
        user_message = (body.get("message") or "").strip()

    if not user_message and not image_file:
        return JsonResponse({"error": "Message or image is required."}, status=400)

    log.chat_received(
        request.session.get("swiggy_conversation_id", "new"),
        has_image=bool(image_file),
        message_preview=user_message,
    )

    user_chat = ChatMessage.objects.create(
        session_key=request.session.session_key,
        sender="user",
        message=user_message,
        image=image_file,
    )
    image_path = user_chat.image.path if user_chat.image else None

    try:
        result = invoke_agent(request, user_message=user_message, image_path=image_path)
    except ImageRequiredError as exc:
        return JsonResponse({"error": str(exc), "tool_usage": "ShowInputSelectionToolNode"}, status=400)
    except ImageNotFoundError:
        return JsonResponse({"error": "Uploaded image could not be found. Please try again."}, status=400)
    except Exception:
        log.error("Unhandled exception while invoking the support agent.")
        return JsonResponse(
            {"error": "Support service is temporarily unavailable. Please try again shortly."},
            status=503,
        )

    bot_reply = result.get("final_response") or (
        "Swiggy Support: Sorry, I could not generate a response. Please try again."
    )
    ChatMessage.objects.create(
        session_key=request.session.session_key,
        sender="bot",
        message=bot_reply,
    )

    return JsonResponse({
        "user_message": user_message,
        "user_image": user_chat.image.url if user_chat.image else None,
        "bot_reply": bot_reply,
        "tool_usage": result.get("tool_usage"),
        "conversation_resolved": result.get("conversation_resolved", False),
        "checkpoint_deleted": result.get("checkpoint_deleted", False),
    })


@csrf_exempt
@require_http_methods(["POST"])
def chat_new(request):
    """Equivalent of main.py's `/new` command: clears the current
    thread's checkpoint and starts a fresh conversation id."""
    if not request.session.session_key:
        request.session.create()

    ChatMessage.objects.filter(session_key=request.session.session_key).delete()
    start_new_conversation(request)

    return JsonResponse({"status": "ok", "message": "Started a new conversation."})

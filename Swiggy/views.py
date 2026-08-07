import json
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from .models import ChatMessage

ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


def home(request):
    return render(request, "home.html")


def index(request):
    """Main Swiggy-style homepage"""
    if not request.session.session_key:
        request.session.create()
    return render(request, 'index.html')


@require_http_methods(["GET"])
def chat_history(request):
    """GET: load previous chat messages for this session (pop-in on page load / reopen)"""
    session_key = request.session.session_key
    if not session_key:
        return JsonResponse({'messages': []})

    messages = ChatMessage.objects.filter(session_key=session_key)
    data = [
        {
            'sender': m.sender,
            'message': m.message,
            'image': m.image.url if m.image else None,
            'time': m.created_at.strftime('%I:%M %p'),
        }
        for m in messages
    ]
    return JsonResponse({'messages': data})


@csrf_exempt
@require_http_methods(["POST"])
def chat_send(request):
    """POST: user sends a message and/or image, we store it + auto-reply (bot), return both"""
    if not request.session.session_key:
        request.session.create()
    session_key = request.session.session_key

    image_file = None
    user_msg = ""

    if request.content_type and request.content_type.startswith('multipart'):
        user_msg = (request.POST.get('message') or '').strip()
        image_file = request.FILES.get('image')

        if image_file:
            if image_file.content_type not in ALLOWED_IMAGE_TYPES:
                return JsonResponse({'error': 'Unsupported image format. Use JPG, PNG, WEBP or GIF.'}, status=400)
            if image_file.size > MAX_IMAGE_SIZE:
                return JsonResponse({'error': 'Image too large. Max size is 5MB.'}, status=400)
    else:
        try:
            body = json.loads(request.body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'error': 'Invalid request'}, status=400)
        user_msg = (body.get('message') or '').strip()

    if not user_msg and not image_file:
        return JsonResponse({'error': 'Empty message'}, status=400)

    # Save user message (text and/or image)
    user_chat = ChatMessage.objects.create(
        session_key=session_key, sender='user', message=user_msg, image=image_file
    )

    # Bot reply
    if image_file and not user_msg:
        reply = "Thanks for the image! Our support team will review it shortly. 📷"
    elif image_file and user_msg:
        reply = generate_reply(user_msg) + " (Got your image too, thanks!)"
    else:
        reply = generate_reply(user_msg)

    ChatMessage.objects.create(session_key=session_key, sender='bot', message=reply)

    return JsonResponse({
        'user_message': user_msg,
        'user_image': user_chat.image.url if user_chat.image else None,
        'bot_reply': reply,
    })


def generate_reply(msg):
    msg_l = msg.lower()
    if 'order' in msg_l:
        return "You can track your order status from the Orders section. Anything specific I can help with?"
    if 'refund' in msg_l:
        return "Refunds are usually processed within 3-5 business days. Want me to raise a request?"
    if 'hi' in msg_l or 'hello' in msg_l:
        return "Hey there! 👋 How can I help you today?"
    return "Thanks for reaching out! Our support team will get back to you shortly."

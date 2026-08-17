from django.urls import path

from . import views

app_name = "customersupport"

urlpatterns = [
    path("", views.swiggy_index, name="swiggy_index"),
    path("zepto/", views.zepto_index, name="zepto_index"),
    path("blinkit/", views.blinkit_index, name="blinkit_index"),
    path("chat/history/", views.chat_history, name="chat_history"),
    path("chat/send/", views.chat_send, name="chat_send"),
    path("chat/new/", views.chat_new, name="chat_new"),
]

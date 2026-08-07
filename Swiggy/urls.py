from django.urls import path
from . import views

app_name = 'swiggy'

urlpatterns = [
    path('', views.index, name='index'),
    path('chat/history/', views.chat_history, name='chat_history'),
    path('chat/send/', views.chat_send, name='chat_send'),
]

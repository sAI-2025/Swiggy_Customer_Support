from django.db import models

class ChatMessage(models.Model):
    SENDER_CHOICES = (
        ('user', 'User'),
        ('bot', 'Bot'),
    )
    session_key = models.CharField(max_length=64, db_index=True)
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES)
    message = models.TextField(blank=True)
    image = models.ImageField(upload_to='uploads/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender}: {self.message[:30] if self.message else '[image]'}"

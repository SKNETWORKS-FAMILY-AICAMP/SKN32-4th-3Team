from django.contrib import admin

from .models import ChatLog, ChatMessage, ChatSession


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ["display_title", "owner", "region", "updated_at"]
    list_filter = ["region"]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ["session", "role", "content", "created_at"]
    list_filter = ["role"]


@admin.register(ChatLog)
class ChatLogAdmin(admin.ModelAdmin):
    list_display = ["question", "region", "has_answer", "user", "created_at"]
    list_filter = ["region", "has_answer"]

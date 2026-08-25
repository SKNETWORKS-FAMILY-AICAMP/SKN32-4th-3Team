from django.contrib import admin

from .models import Board


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ["title", "author", "region", "read_count", "created_at"]
    list_filter = ["region"]
    search_fields = ["title", "content"]

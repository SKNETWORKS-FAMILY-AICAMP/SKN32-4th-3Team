from django.contrib import admin

from .models import Document, QuestionCluster


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ["title", "source_type", "region", "owner", "updated_at"]
    list_filter = ["source_type", "region"]
    search_fields = ["title", "content_text"]


@admin.register(QuestionCluster)
class QuestionClusterAdmin(admin.ModelAdmin):
    list_display = ["representative", "count", "created_at"]
    ordering = ["-count"]

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Member


@admin.register(Member)
class MemberAdmin(UserAdmin):
    list_display = ["username", "display_name", "email", "region", "is_staff", "is_active"]
    list_filter = ["region", "is_staff", "is_active"]
    fieldsets = UserAdmin.fieldsets + (
        ("Ecobot", {"fields": ("display_name", "region", "gender", "age", "phone", "photo")}),
    )

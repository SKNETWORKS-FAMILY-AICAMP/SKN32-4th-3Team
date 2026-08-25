from django.contrib import admin

from .models import Apartment, ApartmentRule, Membership


@admin.register(Apartment)
class ApartmentAdmin(admin.ModelAdmin):
    list_display = ["name", "region", "is_registered", "created_at"]
    list_filter = ["region", "is_registered"]
    search_fields = ["name", "address"]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["member", "apartment", "role", "status", "is_primary", "applied_at"]
    list_filter = ["role", "status"]


@admin.register(ApartmentRule)
class ApartmentRuleAdmin(admin.ModelAdmin):
    list_display = ["apartment", "category", "status", "source_level", "created_at"]
    list_filter = ["status", "category", "source_level"]


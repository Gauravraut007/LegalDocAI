from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("email", "full_name", "is_staff", "is_active", "date_joined", "last_login_ip")
    list_filter = ("is_staff", "is_active")
    search_fields = ("email", "full_name")
    readonly_fields = ("id", "date_joined", "last_login", "last_login_ip")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "avatar")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser",
                                     "groups", "user_permissions")}),
        ("Audit", {"fields": ("id", "date_joined", "last_login", "last_login_ip")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "full_name", "password1", "password2",
                       "is_active", "is_staff"),
        }),
    )

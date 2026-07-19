from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Person


@admin.register(Person)
class PersonAdmin(UserAdmin):
    model = Person
    ordering = ["name"]
    list_display = ["name", "email", "campus", "is_manager", "is_staff", "is_active"]
    list_filter = ["is_manager", "is_staff", "is_active", "campus"]
    search_fields = ["name", "email"]
    filter_horizontal = ["groups", "user_permissions"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Dados pessoais",
            {"fields": ("name", "phone", "campus", "baptized", "baptized_at", "joined_at")},
        ),
        (
            "Permissões",
            {
                "fields": (
                    "is_manager",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "name", "password1", "password2"),
            },
        ),
    )

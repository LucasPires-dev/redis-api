import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from .managers import PersonManager


class Person(AbstractBaseUser, PermissionsMixin):
    """A church member. Also the login identity — there is no separate 'User' model:
    registering a person already creates their access account."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True)
    campus = models.ForeignKey(
        "churches.Campus", null=True, blank=True, on_delete=models.SET_NULL, related_name="people"
    )
    baptized = models.BooleanField(default=False)
    baptized_at = models.DateField(null=True, blank=True)
    joined_at = models.DateField(null=True, blank=True)

    # Gestor da Mega Igreja — permissão global, independente de qualquer liderança
    # de ministério (essas ficam em apps.churches.MinistryLeadership/MinistryGeneralLeadership,
    # e podem mudar de pessoa a qualquer momento).
    is_manager = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = PersonManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

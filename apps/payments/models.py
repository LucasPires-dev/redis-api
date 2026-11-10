import uuid

from django.conf import settings
from django.db import models


class EventTicketConfig(models.Model):
    """Optional registration/ticket config for an Event. One-to-one: an event
    either has tickets or it doesn't."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.OneToOneField(
        "production.Event", on_delete=models.CASCADE, related_name="ticket_config"
    )
    price_cents = models.PositiveIntegerField(default=0, help_text="0 = gratuito com inscrição")
    max_capacity = models.PositiveIntegerField(null=True, blank=True, help_text="null = ilimitado")
    registration_deadline = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True, help_text="O que o ingresso inclui")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        price = f"R$ {self.price_cents / 100:.2f}" if self.price_cents else "Gratuito"
        return f"{self.event.title} — {price}"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    CONFIRMED = "confirmed", "Confirmado"
    CANCELLED = "cancelled", "Cancelado"
    REFUNDED = "refunded", "Reembolsado"


class EventRegistration(models.Model):
    """A person's registration for an event with ticket_config."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        "production.Event", on_delete=models.CASCADE, related_name="registrations"
    )
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_registrations"
    )
    payment_status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    asaas_payment_id = models.CharField(max_length=100, blank=True)
    payment_url = models.URLField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "person"], name="unique_registration_per_event_person"
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.person} — {self.event.title} ({self.get_payment_status_display()})"

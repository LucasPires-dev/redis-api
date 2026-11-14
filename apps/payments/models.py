import secrets
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


# Statuses that hold a spot. Cancelled/refunded registrations are kept as history
# and don't block the person from registering again.
ACTIVE_STATUSES = [PaymentStatus.PENDING, PaymentStatus.CONFIRMED]


# Ticket codes: no 0/O or 1/I, so a code read off a screen is typed right.
TICKET_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TICKET_CODE_LENGTH = 10


def new_ticket_code():
    while True:
        code = "".join(secrets.choice(TICKET_CODE_ALPHABET) for _ in range(TICKET_CODE_LENGTH))
        if not EventRegistration.objects.filter(ticket_code=code).exists():
            return code


class EventRegistration(models.Model):
    """A person's registration for an event with ticket_config.

    `ticket_code` is what the person shows at the door (as a QR code or typed);
    `checked_in_at` records that they came."""

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
    # Price charged for this registration, frozen at registration time: revenue
    # must not change when the organizers later edit the ticket price.
    amount_cents = models.PositiveIntegerField(default=0)
    asaas_payment_id = models.CharField(max_length=100, blank=True)
    payment_url = models.URLField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    ticket_code = models.CharField(max_length=16, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ticket_code"], condition=~models.Q(ticket_code=""), name="unique_ticket_code"
            ),
            # One active registration per person and event (also guards against
            # a double click creating two).
            models.UniqueConstraint(
                fields=["event", "person"],
                condition=models.Q(payment_status__in=ACTIVE_STATUSES),
                name="unique_active_registration_per_event_person",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.person} — {self.event.title} ({self.get_payment_status_display()})"

    def save(self, *args, **kwargs):
        if not self.ticket_code:
            self.ticket_code = new_ticket_code()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "ticket_code"}
        super().save(*args, **kwargs)

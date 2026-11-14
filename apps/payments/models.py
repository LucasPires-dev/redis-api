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
    # How many companions (each with their own ticket) one person may bring.
    max_companions = models.PositiveSmallIntegerField(default=0)
    # Open to anyone with the link (/evento/<id>), not only the campus.
    is_public = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        price = f"R$ {self.price_cents / 100:.2f}" if self.price_cents else "Gratuito"
        return f"{self.event.title} — {price}"


class EventTicketType(models.Model):
    """A kind of ticket of an event ("Inteira", "Meia", "1º lote"). With none,
    the event has a single ticket at EventTicketConfig.price_cents. A batch
    ("lote") is a type with a sales window."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    config = models.ForeignKey(EventTicketConfig, on_delete=models.CASCADE, related_name="types")
    name = models.CharField(max_length=80)
    price_cents = models.PositiveIntegerField(default=0)
    capacity = models.PositiveIntegerField(null=True, blank=True, help_text="null = sem limite próprio")
    sales_start = models.DateTimeField(null=True, blank=True)
    sales_end = models.DateTimeField(null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "price_cents", "created_at"]

    def __str__(self):
        return f"{self.config.event.title} — {self.name}"

    def on_sale(self, now) -> bool:
        return (self.sales_start is None or self.sales_start <= now) and (
            self.sales_end is None or now < self.sales_end
        )


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
        if not (
            EventRegistration.objects.filter(ticket_code=code).exists()
            or EventRegistrationGuest.objects.filter(ticket_code=code).exists()
        ):
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
    ticket_type = models.ForeignKey(
        EventTicketType, null=True, blank=True, on_delete=models.PROTECT, related_name="registrations"
    )
    # The person plus their companions: what the registration takes of the capacity.
    seats = models.PositiveSmallIntegerField(default=1)
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


class EventRegistrationGuest(models.Model):
    """A companion brought by the person who registered: their own ticket and
    check-in, under the same payment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.ForeignKey(EventRegistration, on_delete=models.CASCADE, related_name="guests")
    name = models.CharField(max_length=150)
    ticket_code = models.CharField(max_length=16, unique=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.name} (acompanhante de {self.registration.person})"

    def save(self, *args, **kwargs):
        if not self.ticket_code:
            self.ticket_code = new_ticket_code()
        super().save(*args, **kwargs)

    # The same interface as a registration, for the check-in at the door.
    @property
    def payment_status(self):
        return self.registration.payment_status

    @property
    def event_id(self):
        return self.registration.event_id


class EventWaitlistEntry(models.Model):
    """Someone waiting for a place at a sold-out event. When places open, the
    first in line gets them held until `offer_expires_at` (see waitlist.py)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("production.Event", on_delete=models.CASCADE, related_name="waitlist")
    person = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    seats = models.PositiveSmallIntegerField(default=1)
    offer_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["event", "person"], name="unique_event_waitlist_person"),
        ]

    def __str__(self):
        return f"{self.person} — fila de {self.event}"

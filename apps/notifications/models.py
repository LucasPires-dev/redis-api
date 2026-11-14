import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Notification(models.Model):
    """Something a person should know about, shown in their notification center.

    `link` is an app path (e.g. "/app/cursos/matriculas") the notification opens.
    `dedupe_key`, when set, makes the notification unique per recipient, so the
    same fact (a seat offer, a payment) never notifies twice, even if the code
    that raises it runs again (repeated webhooks, lazy refreshes, reminders).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Reverse access: person.notifications.all()
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=48)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=300, blank=True)
    dedupe_key = models.CharField(max_length=150, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    # When it also went out by email (None: not sent, or not to be sent).
    emailed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "-created_at"], name="notification_recipient_recent"),
            models.Index(
                fields=["recipient"], condition=Q(read_at__isnull=True), name="notification_unread"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "dedupe_key"],
                condition=~Q(dedupe_key=""),
                name="unique_notification_dedupe_key",
            ),
        ]

    def __str__(self):
        return f"{self.recipient} — {self.title}"


class NotificationPreference(models.Model):
    """What a person receives by email and by push, by category (see
    categories.py). Stores the categories turned *off*, so a new category
    starts on for everyone."""

    person = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preference"
    )
    email_off = models.JSONField(default=list, blank=True)
    push_off = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Preferências de {self.person}"

    def wants_email(self, category) -> bool:
        return category is not None and category not in self.email_off

    def wants_push(self, category) -> bool:
        return category is not None and category not in self.push_off


class PushSubscription(models.Model):
    """A browser or phone that receives this person's notifications as push
    (Web Push). One per device; dropped when the push service says it's gone."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions"
    )
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.person} — {self.user_agent[:40] or self.endpoint[:40]}"

    def as_info(self):
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}

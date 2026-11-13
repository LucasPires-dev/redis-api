from django.conf import settings
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .categories import CATEGORIES
from .models import Notification, NotificationPreference
from .serializers import NotificationSerializer


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The signed-in person's notifications, newest first (paginated).

    ?unread=1 lists only the unread ones. `unread-count` feeds the bell;
    `read` marks one as read and `read-all` all of them. `preferences` reads and
    changes what the person receives by email (PATCH {"email": {category: bool}}).
    """

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Notification.objects.filter(recipient=self.request.user)
        if self.request.query_params.get("unread") in ("1", "true"):
            qs = qs.filter(read_at__isnull=True)
        return qs

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = Notification.objects.filter(recipient=request.user, read_at__isnull=True).count()
        return Response({"count": count})

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        updated = Notification.objects.filter(recipient=request.user, read_at__isnull=True).update(
            read_at=timezone.now()
        )
        return Response({"updated": updated})

    @action(detail=False, methods=["get", "patch"])
    def preferences(self, request):
        preference, _ = NotificationPreference.objects.get_or_create(person=request.user)
        if request.method == "PATCH":
            choices = request.data.get("email")
            if not isinstance(choices, dict) or not all(
                key in CATEGORIES and isinstance(value, bool) for key, value in choices.items()
            ):
                raise ValidationError({"email": "Informe as categorias com verdadeiro ou falso."})
            off = set(preference.email_off)
            for key, wanted in choices.items():
                (off.discard if wanted else off.add)(key)
            preference.email_off = sorted(off)
            preference.save(update_fields=["email_off", "updated_at"])
        return Response(
            {
                "email_available": settings.NOTIFICATION_EMAILS_ENABLED,
                "email_address": request.user.email,
                "categories": [
                    {
                        "key": key,
                        "label": cat["label"],
                        "description": cat["description"],
                        "email": key not in preference.email_off,
                    }
                    for key, cat in CATEGORIES.items()
                ],
            }
        )

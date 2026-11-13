from django.conf import settings
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .categories import CATEGORIES
from .models import Notification, NotificationPreference, PushSubscription
from .serializers import NotificationSerializer, PushSubscriptionInputSerializer

CHANNELS = ("email", "push")


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The signed-in person's notifications, newest first (paginated).

    ?unread=1 lists only the unread ones. `unread-count` feeds the bell;
    `read` marks one as read and `read-all` all of them. `preferences` reads and
    changes what the person receives by email and push (PATCH {"email"|"push":
    {category: bool}}); `push-subscriptions` registers this device for push.
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
            changed = []
            for channel in CHANNELS:
                choices = request.data.get(channel)
                if choices is None:
                    continue
                if not isinstance(choices, dict) or not all(
                    key in CATEGORIES and isinstance(value, bool) for key, value in choices.items()
                ):
                    raise ValidationError({channel: "Informe as categorias com verdadeiro ou falso."})
                field = f"{channel}_off"
                off = set(getattr(preference, field))
                for key, wanted in choices.items():
                    (off.discard if wanted else off.add)(key)
                setattr(preference, field, sorted(off))
                changed.append(field)
            if changed:
                preference.save(update_fields=[*changed, "updated_at"])
        return Response(
            {
                "email_available": settings.NOTIFICATION_EMAILS_ENABLED,
                "email_address": request.user.email,
                "push_available": settings.NOTIFICATION_PUSH_ENABLED,
                "push_public_key": settings.VAPID_PUBLIC_KEY if settings.NOTIFICATION_PUSH_ENABLED else "",
                "categories": [
                    {
                        "key": key,
                        "label": cat["label"],
                        "description": cat["description"],
                        "email": key not in preference.email_off,
                        "push": key not in preference.push_off,
                    }
                    for key, cat in CATEGORIES.items()
                ],
            }
        )

    @action(detail=False, methods=["post", "delete"], url_path="push-subscriptions")
    def push_subscriptions(self, request):
        """This device's push subscription (the browser's PushSubscription JSON).
        POST saves it for the signed-in person; DELETE {endpoint} forgets it."""
        if request.method == "DELETE":
            endpoint = request.data.get("endpoint", "")
            PushSubscription.objects.filter(person=request.user, endpoint=endpoint).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        ser = PushSubscriptionInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        # The same device signing in with another account now notifies that one.
        PushSubscription.objects.update_or_create(
            endpoint=data["endpoint"],
            defaults={
                "person": request.user,
                "p256dh": data["keys"]["p256dh"],
                "auth": data["keys"]["auth"],
                "user_agent": request.headers.get("User-Agent", "")[:300],
            },
        )
        return Response(status=status.HTTP_201_CREATED)

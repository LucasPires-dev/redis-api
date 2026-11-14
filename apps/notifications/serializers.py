from urllib.parse import urlsplit

from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "body", "link", "read_at", "created_at"]
        read_only_fields = fields


# Web Push services of Chrome/Edge/Android (FCM), Firefox, Safari/iOS and Windows.
PUSH_SERVICE_HOSTS = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "push.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)


class PushKeysSerializer(serializers.Serializer):
    p256dh = serializers.CharField(max_length=200)
    auth = serializers.CharField(max_length=100)


class PushSubscriptionInputSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=1000)
    keys = PushKeysSerializer()

    def validate_endpoint(self, value):
        # The server POSTs to this URL: only the browsers' push services, never
        # an arbitrary (possibly internal) address.
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not any(host == h or host.endswith("." + h) for h in PUSH_SERVICE_HOSTS):
            raise serializers.ValidationError("Endereço de push inválido.")
        return value

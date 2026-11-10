from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.production.models import Event

from .models import EventRegistration, EventTicketConfig

Person = get_user_model()


class EventTicketConfigSerializer(serializers.ModelSerializer):
    event_id = serializers.PrimaryKeyRelatedField(
        queryset=Event.objects.all(), source="event", write_only=True
    )

    class Meta:
        model = EventTicketConfig
        fields = [
            "id",
            "event_id",
            "price_cents",
            "max_capacity",
            "registration_deadline",
            "description",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate_event_id(self, event):
        # Permission is checked against the config's current event, so moving it to
        # another event would bypass that check (onto an event the user doesn't manage).
        if self.instance is not None and event.pk != self.instance.event_id:
            raise serializers.ValidationError("Não é possível mover o ingresso para outro evento.")
        return event


class RegistrationPersonMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = ["id", "name", "email"]


class EventRegistrationSerializer(serializers.ModelSerializer):
    person = RegistrationPersonMiniSerializer(read_only=True)
    event_id = serializers.PrimaryKeyRelatedField(
        queryset=Event.objects.all(), source="event", write_only=True
    )

    class Meta:
        model = EventRegistration
        fields = [
            "id",
            "event_id",
            "person",
            "payment_status",
            "payment_url",
            "paid_at",
            "created_at",
        ]
        read_only_fields = ["payment_status", "payment_url", "paid_at", "created_at"]


class RegistrationStatsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    confirmed = serializers.IntegerField()
    pending = serializers.IntegerField()
    cancelled = serializers.IntegerField()
    capacity = serializers.IntegerField(allow_null=True)
    revenue_cents = serializers.IntegerField()

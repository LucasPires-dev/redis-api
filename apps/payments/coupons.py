"""Discount coupons for paid event registrations and course enrollments (cells
never charge anything, so they have none).

A coupon belongs to one event or one course class and is managed by whoever
manages that event (or the class's finances). It takes a percentage or a fixed
amount off what the person would pay; the discount is frozen on the
registration/enrollment. `max_uses` counts only the registrations and
enrollments still active: a cancelled one gives its use back.
"""

import re

from django.db.models import ProtectedError, RestrictedError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.production.models import Event
from apps.production.permissions import can_manage_event, scope_events

from .models import ACTIVE_STATUSES, Coupon, CouponKind

# Asaas doesn't accept charges below R$ 5,00.
MIN_CHARGE_CENTS = 500
CODE_RE = re.compile(r"^[A-Z0-9_-]{3,30}$")


def normalize(code):
    return (code or "").strip().upper()


def label(coupon):
    if coupon.kind == CouponKind.PERCENT:
        return f"{coupon.percent}% de desconto"
    return f"R$ {coupon.amount_cents / 100:.2f} de desconto".replace(".", ",")


def uses(coupon):
    """Active registrations/enrollments that used the coupon."""
    if coupon.event_id:
        return coupon.event_registrations.filter(payment_status__in=ACTIVE_STATUSES).count()
    from apps.courses.choices import SEAT_HOLDING_PAYMENT_STATUSES, EnrollmentStatus

    return (
        coupon.enrollments.filter(payment_status__in=SEAT_HOLDING_PAYMENT_STATUSES)
        .exclude(status=EnrollmentStatus.DROPPED)
        .count()
    )


def _check_usable(coupon, now=None):
    now = now or timezone.now()
    if coupon.valid_until and now > coupon.valid_until:
        raise ValidationError({"coupon_code": "Este cupom expirou."})
    if coupon.max_uses is not None and uses(coupon) >= coupon.max_uses:
        raise ValidationError({"coupon_code": "Este cupom já atingiu o limite de usos."})


def find(code, *, event=None, course_class=None):
    """The active coupon with this code for the event (or class), or a 400."""
    code = normalize(code)
    target = {"event": event} if event is not None else {"course_class": course_class}
    coupon = Coupon.objects.filter(code=code, is_active=True, **target).first() if code else None
    if coupon is None:
        raise ValidationError({"coupon_code": "Cupom inválido."})
    _check_usable(coupon)
    return coupon


def discount_for(coupon, amount_cents):
    """What the coupon takes off `amount_cents`. The rest must be zero or at
    least R$ 5,00 (the smallest charge Asaas accepts)."""
    if coupon is None:
        return 0
    if not amount_cents:
        raise ValidationError({"coupon_code": "Esta inscrição é gratuita: não precisa de cupom."})
    if coupon.kind == CouponKind.PERCENT:
        discount = amount_cents * coupon.percent // 100
    else:
        discount = min(coupon.amount_cents, amount_cents)
    if 0 < amount_cents - discount < MIN_CHARGE_CENTS:
        raise ValidationError(
            {"coupon_code": "Com este cupom o valor ficaria abaixo de R$ 5,00, o mínimo para cobrança."}
        )
    return discount


def claim(coupon):
    """Re-checks the coupon under a row lock (inside the caller's transaction),
    so two people can't both take its last use."""
    if coupon is None:
        return
    locked = Coupon.objects.select_for_update().get(pk=coupon.pk)
    if not locked.is_active:
        raise ValidationError({"coupon_code": "Cupom inválido."})
    _check_usable(locked)


# ── Who manages ─────────────────────────────────────────────────────────────


def _class(pk):
    from apps.courses.models import CourseClass

    return CourseClass.objects.select_related("course", "campus").filter(pk=pk).first()


def can_manage_target(person, *, event=None, course_class=None):
    if event is not None:
        return can_manage_event(person, event)
    from apps.courses.permissions import can_manage_class_finances

    return can_manage_class_finances(person, course_class)


def can_manage_coupon(person, coupon):
    return can_manage_target(person, event=coupon.event, course_class=coupon.course_class)


# ── API ─────────────────────────────────────────────────────────────────────


class CouponSerializer(serializers.ModelSerializer):
    event_id = serializers.UUIDField(required=False, allow_null=True)
    course_class_id = serializers.UUIDField(required=False, allow_null=True)
    uses = serializers.SerializerMethodField()
    label = serializers.SerializerMethodField()

    class Meta:
        model = Coupon
        fields = [
            "id",
            "event_id",
            "course_class_id",
            "code",
            "kind",
            "percent",
            "amount_cents",
            "max_uses",
            "valid_until",
            "is_active",
            "uses",
            "label",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_uses(self, obj):
        return uses(obj)

    def get_label(self, obj):
        return label(obj)

    def validate_code(self, value):
        code = normalize(value)
        if not CODE_RE.match(code):
            raise serializers.ValidationError("Use de 3 a 30 letras, números, - ou _ (sem espaços).")
        return code

    def validate(self, attrs):
        if self.instance is not None:
            # The target never changes.
            attrs.pop("event_id", None)
            attrs.pop("course_class_id", None)
            event_id, class_id = self.instance.event_id, self.instance.course_class_id
        else:
            event_id, class_id = attrs.get("event_id"), attrs.get("course_class_id")
            if bool(event_id) == bool(class_id):
                raise serializers.ValidationError({"detail": "Informe o evento ou a turma do cupom."})

        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        percent = attrs.get("percent", getattr(self.instance, "percent", None))
        amount = attrs.get("amount_cents", getattr(self.instance, "amount_cents", None))
        if kind == CouponKind.PERCENT:
            if not percent or not 1 <= percent <= 100:
                raise serializers.ValidationError({"percent": "Informe um percentual de 1 a 100."})
            attrs["amount_cents"] = None
        elif kind == CouponKind.FIXED:
            if not amount or amount < 1:
                raise serializers.ValidationError({"amount_cents": "Informe o valor do desconto."})
            attrs["percent"] = None
        if attrs.get("max_uses") == 0:
            raise serializers.ValidationError({"max_uses": "O limite precisa ser de pelo menos 1 uso."})

        code = attrs.get("code", getattr(self.instance, "code", ""))
        same = Coupon.objects.filter(code=code, event_id=event_id, course_class_id=class_id)
        if self.instance is not None:
            same = same.exclude(pk=self.instance.pk)
        if same.exists():
            raise serializers.ValidationError({"code": "Já existe um cupom com este código."})
        return attrs


class CouponCheckThrottle(UserRateThrottle):
    """Caps code attempts so coupons can't be guessed by brute force."""

    scope = "coupon_check"


class CouponViewSet(viewsets.ModelViewSet):
    """GET /api/payments/coupons/?event=<id> (or ?course_class=<id>): the
    coupons of an event/class, for whoever manages it. POST/PATCH/DELETE the
    same. POST /check/: a person checks a code before registering."""

    serializer_class = CouponSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Coupon.objects.select_related("event", "course_class", "course_class__course", "course_class__campus")
        if self.action != "list":
            return qs
        event_id = self.request.query_params.get("event")
        class_id = self.request.query_params.get("course_class")
        if event_id:
            event = get_object_or_404(Event, pk=event_id)
            if not can_manage_event(self.request.user, event):
                raise PermissionDenied()
            return qs.filter(event=event)
        if class_id:
            course_class = _class(class_id)
            if course_class is None:
                return qs.none()
            if not can_manage_target(self.request.user, course_class=course_class):
                raise PermissionDenied()
            return qs.filter(course_class=course_class)
        raise ValidationError({"detail": "Informe ?event= ou ?course_class=."})

    def get_object(self):
        coupon = super().get_object()
        if not can_manage_coupon(self.request.user, coupon):
            raise PermissionDenied()
        return coupon

    def perform_create(self, serializer):
        data = serializer.validated_data
        event = course_class = None
        if data.get("event_id"):
            event = Event.objects.filter(pk=data["event_id"]).first()
            if event is None:
                raise ValidationError({"event_id": "Evento não encontrado."})
        else:
            course_class = _class(data["course_class_id"])
            if course_class is None:
                raise ValidationError({"course_class_id": "Turma não encontrada."})
        if not can_manage_target(self.request.user, event=event, course_class=course_class):
            raise PermissionDenied()
        data.pop("event_id", None)
        data.pop("course_class_id", None)
        serializer.save(event=event, course_class=course_class, created_by=self.request.user)

    def destroy(self, request, *args, **kwargs):
        coupon = self.get_object()
        try:
            coupon.delete()
        except (ProtectedError, RestrictedError):
            return Response(
                {"detail": "Este cupom já foi usado. Desative-o em vez de excluir."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="check", throttle_classes=[CouponCheckThrottle])
    def check(self, request):
        code = request.data.get("code", "")
        event_id = request.data.get("event_id")
        class_id = request.data.get("course_class_id")
        if event_id:
            event = scope_events(Event.objects.filter(pk=event_id), request.user).first()
            if event is None:
                raise ValidationError({"event_id": "Evento não encontrado."})
            coupon = find(code, event=event)
        elif class_id:
            course_class = _class(class_id)
            if course_class is None:
                raise ValidationError({"course_class_id": "Turma não encontrada."})
            coupon = find(code, course_class=course_class)
        else:
            raise ValidationError({"detail": "Informe o evento ou a turma."})
        return Response(
            {
                "code": coupon.code,
                "kind": coupon.kind,
                "percent": coupon.percent,
                "amount_cents": coupon.amount_cents,
                "label": label(coupon),
            }
        )

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from apps.churches.serializers import CampusMiniSerializer

from . import billing
from .choices import SEAT_HOLDING_PAYMENT_STATUSES, EnrollmentStatus, TeacherRole
from .models import (
    ClassExpense,
    ClassFeeItem,
    Course,
    CourseClass,
    CourseClassTeacher,
    Enrollment,
    EnrollmentItem,
    EnrollmentPayment,
    Lesson,
    LessonAttendance,
)
from .permissions import can_manage_class, can_manage_class_finances

Person = get_user_model()


class PersonMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = ["id", "name", "email"]


# ── Course ──────────────────────────────────────────────────────────────────


class CourseSerializer(serializers.ModelSerializer):
    campus = CampusMiniSerializer(read_only=True)
    campus_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    class_count = serializers.SerializerMethodField()
    total_enrolled = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = [
            "id", "name", "description", "campus", "campus_id",
            "class_count", "total_enrolled", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_class_count(self, obj):
        return obj.classes.count()

    def get_total_enrolled(self, obj):
        return Enrollment.objects.filter(
            course_class__course=obj,
            status=EnrollmentStatus.ACTIVE,
        ).count()


# ── CourseClass (turma) ─────────────────────────────────────────────────────


class TeacherSerializer(serializers.ModelSerializer):
    person = PersonMiniSerializer(read_only=True)

    class Meta:
        model = CourseClassTeacher
        fields = ["id", "person", "role"]


class TeacherInputSerializer(serializers.Serializer):
    person_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=TeacherRole.choices, default=TeacherRole.TEACHER)


class ClassFeeItemSerializer(serializers.ModelSerializer):
    # Present when editing an existing item; omitted for a new one.
    id = serializers.UUIDField(required=False)
    options = serializers.ListField(
        child=serializers.CharField(max_length=50, allow_blank=True), required=False, max_length=20
    )

    class Meta:
        model = ClassFeeItem
        fields = ["id", "name", "price_cents", "required", "options", "order"]

    def validate_price_cents(self, value):
        if value < 1:
            raise serializers.ValidationError("Informe o valor do item.")
        return value

    def validate_options(self, value):
        cleaned = [option.strip() for option in value if option.strip()]
        if len(set(cleaned)) != len(cleaned):
            raise serializers.ValidationError("As opções não podem se repetir.")
        return cleaned


class CourseClassSerializer(serializers.ModelSerializer):
    campus = CampusMiniSerializer(read_only=True)
    campus_id = serializers.UUIDField(write_only=True)
    course_id = serializers.UUIDField(write_only=True)
    course_name = serializers.CharField(source="course.name", read_only=True)
    teachers = TeacherSerializer(many=True, read_only=True)
    enrolled_count = serializers.SerializerMethodField()
    lesson_count = serializers.SerializerMethodField()
    # Replaced as a whole on write: items sent with an id are updated, without
    # one created, and missing ones removed (enrollments keep their copies).
    fee_items = ClassFeeItemSerializer(many=True, required=False)
    base_price_cents = serializers.IntegerField(read_only=True)
    seats_left = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()
    can_manage_finances = serializers.SerializerMethodField()

    class Meta:
        model = CourseClass
        fields = [
            "id", "course_id", "course_name", "campus", "campus_id",
            "name", "weekday", "start_time", "start_date", "end_date",
            "description", "teachers", "enrolled_count", "lesson_count",
            "self_enrollment", "max_capacity", "enrollment_deadline", "reservation_days",
            "max_installments", "refund_before_start", "fee_items", "base_price_cents",
            "seats_left", "can_manage", "can_manage_finances",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_enrolled_count(self, obj):
        return billing.active_enrollments(obj).count()

    def get_lesson_count(self, obj):
        return obj.lessons.count()

    def get_seats_left(self, obj):
        return billing.seats_left(obj)

    def _user(self):
        request = self.context.get("request")
        return request.user if request else None

    def get_can_manage(self, obj):
        user = self._user()
        return bool(user) and can_manage_class(user, obj)

    def get_can_manage_finances(self, obj):
        user = self._user()
        return bool(user) and can_manage_class_finances(user, obj)

    def validate(self, attrs):
        def current(field, default=None):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, default)

        start, end = current("start_date"), current("end_date")
        if start and end and end < start:
            raise serializers.ValidationError({"end_date": "A data de fim deve ser depois do início."})
        deadline = current("enrollment_deadline")
        if deadline and end and deadline > end:
            raise serializers.ValidationError(
                {"enrollment_deadline": "O prazo de inscrição precisa terminar até o fim da turma."}
            )
        capacity = attrs.get("max_capacity")
        if self.instance and capacity is not None:
            taken = billing.active_enrollments(self.instance).count()
            if capacity < taken:
                raise serializers.ValidationError(
                    {"max_capacity": f"A turma já tem {taken} matrículas ativas."}
                )
        if "fee_items" in attrs:
            items = attrs["fee_items"]
            names = [item["name"].strip().lower() for item in items]
            if len(set(names)) != len(names):
                raise serializers.ValidationError({"fee_items": "Há itens com o mesmo nome."})
            base = sum(item["price_cents"] for item in items if item.get("required", True))
            if 0 < base < billing.MIN_CHARGE_CENTS:
                raise serializers.ValidationError(
                    {"fee_items": "Os itens obrigatórios somam menos que R$ 5,00, o mínimo para cobrança."}
                )
            if self.instance:
                own = {str(i) for i in self.instance.fee_items.values_list("id", flat=True)}
                for item in items:
                    if item.get("id") and str(item["id"]) not in own:
                        raise serializers.ValidationError({"fee_items": "Item inválido para esta turma."})
        return attrs

    def _sync_fee_items(self, course_class, items):
        keep = []
        for order, data in enumerate(items):
            data = {**data, "order": data.get("order", order)}
            item_id = data.pop("id", None)
            if item_id:
                ClassFeeItem.objects.filter(pk=item_id, course_class=course_class).update(**data)
                keep.append(item_id)
            else:
                keep.append(ClassFeeItem.objects.create(course_class=course_class, **data).pk)
        course_class.fee_items.exclude(pk__in=keep).delete()

    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("fee_items", [])
        course_class = super().create(validated_data)
        self._sync_fee_items(course_class, items)
        return course_class

    @transaction.atomic
    def update(self, instance, validated_data):
        items = validated_data.pop("fee_items", None)
        course_class = super().update(instance, validated_data)
        if items is not None:
            self._sync_fee_items(course_class, items)
        return course_class


class OpenClassSerializer(serializers.ModelSerializer):
    """A class a member can enroll in by themselves (the "Cursos abertos" page)."""

    course_name = serializers.CharField(source="course.name", read_only=True)
    course_description = serializers.CharField(source="course.description", read_only=True)
    campus = CampusMiniSerializer(read_only=True)
    fee_items = ClassFeeItemSerializer(many=True, read_only=True)
    base_price_cents = serializers.IntegerField(read_only=True)
    seats_left = serializers.SerializerMethodField()
    my_enrollment = serializers.SerializerMethodField()

    class Meta:
        model = CourseClass
        fields = [
            "id", "course_name", "course_description", "campus", "name", "description",
            "weekday", "start_time", "start_date", "end_date", "max_capacity",
            "enrollment_deadline", "reservation_days", "max_installments", "refund_before_start",
            "fee_items", "base_price_cents", "seats_left", "my_enrollment",
        ]

    def get_seats_left(self, obj):
        return billing.seats_left(obj)

    def get_my_enrollment(self, obj):
        request = self.context.get("request")
        if not request:
            return None
        mine = billing.active_enrollments(obj).filter(person=request.user).first()
        return {"id": str(mine.id), "payment_status": mine.payment_status} if mine else None


# ── Enrollment ──────────────────────────────────────────────────────────────


class EnrollmentItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnrollmentItem
        fields = ["id", "name", "price_cents", "option"]


class EnrollmentPaymentSerializer(serializers.ModelSerializer):
    recorded_by = PersonMiniSerializer(read_only=True)

    class Meta:
        model = EnrollmentPayment
        fields = [
            "id", "method", "amount_cents", "note", "recorded_by", "paid_at",
            "refunded_at", "refund_note",
        ]


class EnrollmentSerializer(serializers.ModelSerializer):
    """The class roster (teachers see it too): who, and whether they're settled."""

    person = PersonMiniSerializer(read_only=True)
    payment_status_display = serializers.CharField(source="get_payment_status_display", read_only=True)

    class Meta:
        model = Enrollment
        fields = [
            "id", "person", "status", "enrolled_at", "completed_at",
            "payment_status", "payment_status_display", "material_delivered_at",
        ]


class EnrollmentFinanceSerializer(EnrollmentSerializer):
    """Everything about an enrollment's money — for the class's coordinators."""

    items = EnrollmentItemSerializer(many=True, read_only=True)
    payments = EnrollmentPaymentSerializer(many=True, read_only=True)
    scholarship_granted_by = PersonMiniSerializer(read_only=True)
    paid_cents = serializers.IntegerField(read_only=True)
    balance_cents = serializers.IntegerField(read_only=True)

    class Meta(EnrollmentSerializer.Meta):
        fields = EnrollmentSerializer.Meta.fields + [
            "items", "payments", "gross_cents", "discount_cents", "amount_cents",
            "paid_cents", "balance_cents", "refunded_cents", "scholarship_reason",
            "scholarship_granted_by", "installments", "payment_url", "expires_at",
            "paid_at", "has_overdue_installment",
        ]


class MyEnrollmentSerializer(EnrollmentFinanceSerializer):
    """A person's own enrollment, with the class it's in."""

    course_class = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()

    class Meta(EnrollmentFinanceSerializer.Meta):
        fields = EnrollmentFinanceSerializer.Meta.fields + ["course_class", "can_cancel"]

    def get_course_class(self, obj):
        cc = obj.course_class
        return {
            "id": str(cc.id),
            "name": cc.name,
            "course_id": str(cc.course_id),
            "course_name": cc.course.name,
            "campus": cc.campus.name,
            "weekday": cc.weekday,
            "start_time": cc.start_time.strftime("%H:%M") if cc.start_time else None,
            "start_date": cc.start_date,
            "end_date": cc.end_date,
            "max_installments": cc.max_installments,
        }

    def get_can_cancel(self, obj):
        # By themselves only while nothing was paid; paid ones go through the coordinators.
        return obj.payment_status in SEAT_HOLDING_PAYMENT_STATUSES and obj.paid_cents == 0


class FeeChoiceSerializer(serializers.Serializer):
    fee_item_id = serializers.UUIDField()
    option = serializers.CharField(required=False, allow_blank=True, max_length=50)


class EnrollInputSerializer(serializers.Serializer):
    """A member enrolling themselves: the optional items they want (required ones
    are always included; items with options need the option) and installments."""

    items = FeeChoiceSerializer(many=True, required=False, default=list)
    installments = serializers.IntegerField(min_value=1, max_value=12, default=1)


class EnrollmentInputSerializer(EnrollInputSerializer):
    """A coordinator enrolling someone, or changing an enrollment's status
    (e.g. marking it completed) when the person is already enrolled."""

    person_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=EnrollmentStatus.choices, required=False)
    # Create the online payment link now; otherwise the enrollment waits for a
    # payment in person (or a link generated later).
    send_charge = serializers.BooleanField(default=False)


class ScholarshipInputSerializer(serializers.Serializer):
    discount_cents = serializers.IntegerField(min_value=0)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=200)


class ManualPaymentInputSerializer(serializers.Serializer):
    amount_cents = serializers.IntegerField(min_value=1)
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)


class ChargeInputSerializer(serializers.Serializer):
    installments = serializers.IntegerField(min_value=1, max_value=12, required=False)


class CancelEnrollmentInputSerializer(serializers.Serializer):
    # Refund even after the class started (overrides the class's rule).
    force = serializers.BooleanField(default=False)
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)


class MaterialInputSerializer(serializers.Serializer):
    delivered = serializers.BooleanField()


class ClassExpenseSerializer(serializers.ModelSerializer):
    course_class_id = serializers.PrimaryKeyRelatedField(
        source="course_class", queryset=CourseClass.objects.all(), write_only=True
    )
    created_by = PersonMiniSerializer(read_only=True)

    class Meta:
        model = ClassExpense
        fields = ["id", "course_class_id", "description", "amount_cents", "date", "created_by", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        if self.instance and "course_class" in attrs and attrs["course_class"] != self.instance.course_class:
            raise serializers.ValidationError({"course_class_id": "Não é possível mover uma despesa de turma."})
        return attrs


# ── Lesson ──────────────────────────────────────────────────────────────────


class LessonSerializer(serializers.ModelSerializer):
    course_class_id = serializers.UUIDField(write_only=True)
    attendance_count = serializers.SerializerMethodField()
    total_enrolled = serializers.SerializerMethodField()

    class Meta:
        model = Lesson
        fields = [
            "id", "course_class_id", "order", "title", "date", "notes",
            "attendance_count", "total_enrolled", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_attendance_count(self, obj):
        return obj.attendances.filter(present=True).count()

    def get_total_enrolled(self, obj):
        return obj.course_class.enrollments.filter(status=EnrollmentStatus.ACTIVE).count()


# ── Attendance ──────────────────────────────────────────────────────────────


class LessonAttendanceSerializer(serializers.ModelSerializer):
    person = PersonMiniSerializer(read_only=True)

    class Meta:
        model = LessonAttendance
        fields = ["id", "person", "present", "created_at"]


class BulkAttendanceInputSerializer(serializers.Serializer):
    """Receives the full attendance list for one lesson in a single request."""
    attendances = serializers.ListField(
        child=serializers.DictField(), min_length=0
    )

    def validate_attendances(self, value):
        for item in value:
            if "person_id" not in item:
                raise serializers.ValidationError("Each item needs person_id.")
            if "present" not in item:
                item["present"] = True
        return value

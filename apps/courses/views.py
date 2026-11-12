import csv

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, serializers as drf_serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.people.cpf import apply_cpf

from . import billing, certificates, completion
from .choices import EnrollmentStatus
from .models import (
    ClassExpense,
    Course,
    CourseClass,
    CourseClassTeacher,
    Enrollment,
    Lesson,
    LessonAttendance,
)
from .permissions import (
    CourseClassPermission,
    CoursePermission,
    LessonPermission,
    can_manage_class,
    can_manage_class_finances,
    scope_classes,
    scope_courses,
)
from .serializers import (
    BulkAttendanceInputSerializer,
    CancelEnrollmentInputSerializer,
    ChargeInputSerializer,
    CompleteClassInputSerializer,
    ClassExpenseSerializer,
    CourseClassSerializer,
    CourseSerializer,
    EnrollInputSerializer,
    EnrollmentFinanceSerializer,
    EnrollmentInputSerializer,
    EnrollmentSerializer,
    LessonAttendanceSerializer,
    LessonSerializer,
    ManualPaymentInputSerializer,
    MaterialInputSerializer,
    MyEnrollmentSerializer,
    OpenClassSerializer,
    PersonMiniSerializer,
    ScholarshipInputSerializer,
    TeacherInputSerializer,
    TeacherSerializer,
)

Person = get_user_model()

PEOPLE_SEARCH_LIMIT = 20


class PaymentProviderUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Não foi possível falar com o provedor de pagamentos agora. Tente novamente em alguns minutos."
    default_code = "payment_provider_unavailable"


def _billing(call, *args, **kwargs):
    """Runs a billing operation, turning a provider failure into a 503."""
    try:
        return call(*args, **kwargs)
    except billing.ProviderUnavailable as exc:
        raise PaymentProviderUnavailable(exc.message)


def _search_people(qs, request):
    term = (request.query_params.get("q") or "").strip()
    if len(term) < 2:
        return Response([])
    qs = qs.filter(Q(name__icontains=term) | Q(email__icontains=term)).order_by("name")
    return Response(PersonMiniSerializer(qs[:PEOPLE_SEARCH_LIMIT], many=True).data)


def _brl(cents):
    return f"{cents / 100:.2f}".replace(".", ",")


class CourseViewSet(viewsets.ModelViewSet):
    serializer_class = CourseSerializer
    permission_classes = [CoursePermission]

    def get_queryset(self):
        qs = Course.objects.select_related("campus").prefetch_related("classes")
        return scope_courses(qs, self.request.user)

    def perform_create(self, serializer):
        from apps.churches.models import Church
        user = self.request.user
        church = user.campus.church if user.campus_id else Church.objects.first()
        serializer.save(church=church, created_by=user)


class CourseClassViewSet(viewsets.ModelViewSet):
    serializer_class = CourseClassSerializer
    permission_classes = [CourseClassPermission]

    def get_queryset(self):
        qs = CourseClass.objects.select_related("course", "campus").prefetch_related(
            "teachers__person", "enrollments", "lessons", "fee_items"
        )
        course_id = self.request.query_params.get("course_id")
        if course_id:
            qs = qs.filter(course_id=course_id)
        # ?teaching=1: only the classes the person teaches ("Minhas turmas"),
        # even for a coordinator who sees many more.
        if self.request.query_params.get("teaching") in ("1", "true"):
            qs = qs.filter(teachers__person=self.request.user)
        return scope_classes(qs, self.request.user)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def _require_finances(self, course_class):
        if not can_manage_class_finances(self.request.user, course_class):
            raise PermissionDenied("Só a coordenação do curso cuida do financeiro da turma.")

    # ── Teachers ────────────────────────────────────────────────────────

    @action(detail=True, methods=["get", "post"], url_path="teachers")
    def teachers(self, request, pk=None):
        course_class = self.get_object()
        if request.method == "GET":
            return Response(
                TeacherSerializer(course_class.teachers.select_related("person"), many=True).data
            )
        if not can_manage_class(request.user, course_class):
            raise PermissionDenied()
        ser = TeacherInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=ser.validated_data["person_id"])
        obj, created = CourseClassTeacher.objects.get_or_create(
            course_class=course_class,
            person=person,
            defaults={"role": ser.validated_data["role"]},
        )
        if not created:
            obj.role = ser.validated_data["role"]
            obj.save(update_fields=["role"])
        return Response(
            TeacherSerializer(obj).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["delete"], url_path=r"teachers/(?P<teacher_id>[^/.]+)")
    def remove_teacher(self, request, pk=None, teacher_id=None):
        course_class = self.get_object()
        if not can_manage_class(request.user, course_class):
            raise PermissionDenied()
        CourseClassTeacher.objects.filter(
            course_class=course_class, person_id=teacher_id
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── Enrollments ─────────────────────────────────────────────────────

    @action(detail=True, methods=["get", "post"], url_path="enrollments")
    def enrollments(self, request, pk=None):
        """GET: the roster (expired reservations released first). POST: enroll
        someone (coordinators), or change the status of an existing enrollment
        (e.g. completed) — teachers may do that too."""
        course_class = self.get_object()
        if request.method == "GET":
            return self._roster(course_class)

        if not can_manage_class(request.user, course_class):
            raise PermissionDenied()
        ser = EnrollmentInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        person = get_object_or_404(Person, pk=data["person_id"])

        existing = billing.active_enrollments(course_class).filter(person=person).first()
        if existing:
            new_status = data.get("status")
            if new_status and new_status != existing.status:
                if new_status == EnrollmentStatus.DROPPED:
                    raise drf_serializers.ValidationError(
                        {"status": "Para tirar alguém da turma, cancele a matrícula."}
                    )
                existing.status = new_status
                existing.completed_at = timezone.now() if new_status == EnrollmentStatus.COMPLETED else None
                existing.save(update_fields=["status", "completed_at", "updated_at"])
                if new_status == EnrollmentStatus.COMPLETED:
                    certificates.ensure_code(existing)
            return Response(EnrollmentSerializer(existing).data, status=status.HTTP_200_OK)

        # A paid enrollment is a financial act: only the coordinators.
        if course_class.is_paid and not can_manage_class_finances(request.user, course_class):
            raise PermissionDenied("Só a coordenação do curso matricula em turmas pagas.")
        apply_cpf(person, data.get("cpf", ""))
        enrollment = _billing(
            billing.enroll,
            course_class,
            person,
            data["items"],
            data["installments"],
            self_service=False,
            send_charge=data["send_charge"],
        )
        return Response(EnrollmentSerializer(enrollment).data, status=status.HTTP_201_CREATED)

    def _roster(self, course_class):
        """The class's enrollments with each student's attendance."""
        billing.expire_overdue(course_class.enrollments.all())
        enrollments = course_class.enrollments.select_related("person")
        context = {"attendance": completion.attendance_by_person(course_class)}
        return Response(EnrollmentSerializer(enrollments, many=True, context=context).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """Closes the class for the given students: their enrollments become
        completed. Teachers may do it too (as with any status change)."""
        course_class = self.get_object()
        if not can_manage_class(request.user, course_class):
            raise PermissionDenied()
        ser = CompleteClassInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        completion.complete(course_class, ser.validated_data["person_ids"])
        return self._roster(course_class)

    @action(detail=True, methods=["delete"], url_path=r"enrollments/(?P<person_id>[^/.]+)")
    def remove_enrollment(self, request, pk=None, person_id=None):
        """Deletes a mistaken enrollment. One with money received must be
        cancelled instead (refund), so the financial history is kept."""
        course_class = self.get_object()
        if not can_manage_class(request.user, course_class):
            raise PermissionDenied()
        enrollments = Enrollment.objects.filter(course_class=course_class, person_id=person_id)
        if any(e.payments.exists() for e in enrollments):
            raise drf_serializers.ValidationError(
                {"detail": "Esta matrícula tem pagamentos. Cancele-a para registrar o reembolso."}
            )
        for enrollment in enrollments:
            if enrollment.asaas_payment_id:
                _billing(billing.cancel, enrollment, by_coordinator=True)
        enrollments.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── Finances ────────────────────────────────────────────────────────

    @action(detail=True, methods=["get"])
    def finance(self, request, pk=None):
        course_class = self.get_object()
        self._require_finances(course_class)
        summary = billing.finance_summary(course_class)
        enrollments = course_class.enrollments.select_related(
            "person", "scholarship_granted_by"
        ).prefetch_related("items", "payments__recorded_by")
        return Response(
            {
                "summary": summary,
                "enrollments": EnrollmentFinanceSerializer(enrollments, many=True).data,
            }
        )

    @action(detail=True, methods=["get"], url_path="finance-export")
    def finance_export(self, request, pk=None):
        """CSV (semicolon-separated, Excel-friendly) of the class's enrollments
        and payments, for the church treasury."""
        course_class = self.get_object()
        self._require_finances(course_class)
        billing.expire_overdue(course_class.enrollments.all())
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        filename = f"financeiro-{course_class.course.name}-{course_class.name}.csv".replace(" ", "-")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("﻿")  # BOM: Excel reads the accents right
        writer = csv.writer(response, delimiter=";")
        writer.writerow([
            "Aluno", "E-mail", "Situação", "Itens", "Valor (R$)", "Bolsa (R$)", "A pagar (R$)",
            "Pago (R$)", "Em aberto (R$)", "Reembolsado (R$)", "Parcelas", "Forma",
            "Motivo da bolsa", "Material entregue",
        ])
        enrollments = course_class.enrollments.select_related("person").prefetch_related("items", "payments")
        for e in enrollments:
            methods = sorted({p.get_method_display() for p in e.payments.all()})
            writer.writerow([
                e.person.name,
                e.person.email,
                e.get_payment_status_display(),
                ", ".join(f"{i.name}{f' ({i.option})' if i.option else ''}" for i in e.items.all()),
                _brl(e.gross_cents),
                _brl(e.discount_cents),
                _brl(e.amount_cents),
                _brl(e.paid_cents),
                _brl(e.balance_cents),
                _brl(e.refunded_cents),
                e.installments,
                ", ".join(methods),
                e.scholarship_reason,
                e.material_delivered_at.strftime("%d/%m/%Y") if e.material_delivered_at else "",
            ])
        writer.writerow([])
        writer.writerow(["Despesa", "Data", "Valor (R$)"])
        for expense in course_class.expenses.all():
            writer.writerow([expense.description, expense.date.strftime("%d/%m/%Y"), _brl(expense.amount_cents)])
        return response

    # ── Self-enrollment ─────────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="open", permission_classes=[IsAuthenticated])
    def open(self, request):
        """Classes open for members to enroll themselves, at the person's church."""
        qs = CourseClass.objects.filter(billing.open_classes_filter()).select_related(
            "course", "campus"
        ).prefetch_related("fee_items").order_by("start_date", "course__name", "name")
        church_id = getattr(request.user.campus, "church_id", None)
        if church_id:
            qs = qs.filter(course__church_id=church_id)
        return Response(OpenClassSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="enroll", permission_classes=[IsAuthenticated])
    def enroll(self, request, pk=None):
        """A member enrolls themselves in an open class."""
        course_class = get_object_or_404(
            CourseClass.objects.filter(billing.open_classes_filter()), pk=pk
        )
        church_id = getattr(request.user.campus, "church_id", None)
        if church_id and course_class.course.church_id != church_id:
            raise PermissionDenied()
        ser = EnrollInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        apply_cpf(request.user, ser.validated_data.get("cpf", ""))
        enrollment = _billing(
            billing.enroll,
            course_class,
            request.user,
            ser.validated_data["items"],
            ser.validated_data["installments"],
            self_service=True,
        )
        return Response(
            MyEnrollmentSerializer(enrollment, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    # ── People search (for enrollment/teacher pickers) ──────────────────

    @action(detail=True, methods=["get"], url_path="search-people")
    def search_people(self, request, pk=None):
        self.get_object()  # permission check
        return _search_people(Person.objects.all(), request)


class EnrollmentViewSet(mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Enrollments with their money. ?mine=1 lists the caller's own (any class);
    otherwise the classes they coordinate. Actions: pay (new link), cancel,
    scholarship, manual-payment, material."""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Enrollment.objects.select_related(
            "person", "course_class__course", "course_class__campus", "scholarship_granted_by"
        ).prefetch_related("items", "payments__recorded_by").order_by("-enrolled_at")
        if self.request.query_params.get("mine") in ("1", "true"):
            billing.expire_overdue(Enrollment.objects.filter(person=user))
            return qs.filter(person=user)
        visible_classes = scope_classes(CourseClass.objects.all(), user)
        qs = qs.filter(Q(person=user) | Q(course_class__in=visible_classes))
        course_class_id = self.request.query_params.get("course_class")
        if course_class_id:
            qs = qs.filter(course_class_id=course_class_id)
        return qs

    def get_serializer_class(self):
        return MyEnrollmentSerializer

    def list(self, request, *args, **kwargs):
        # Other people's enrollments only for whoever handles that class's money.
        if request.query_params.get("mine") not in ("1", "true"):
            course_class = get_object_or_404(CourseClass, pk=request.query_params.get("course_class"))
            if not can_manage_class_finances(request.user, course_class):
                raise PermissionDenied()
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        enrollment = self.get_object()
        self._require_owner_or_finances(enrollment)
        return Response(self.get_serializer(enrollment).data)

    def _is_finances(self, enrollment):
        return can_manage_class_finances(self.request.user, enrollment.course_class)

    def _require_owner_or_finances(self, enrollment):
        if enrollment.person_id != self.request.user.pk and not self._is_finances(enrollment):
            raise PermissionDenied()

    def _require_finances(self, enrollment):
        if not self._is_finances(enrollment):
            raise PermissionDenied("Só a coordenação do curso cuida do financeiro da turma.")

    def _respond(self, enrollment):
        enrollment.refresh_from_db()
        return Response(self.get_serializer(enrollment).data)

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        """A new payment link for what is still owed (the person or the coordinators)."""
        enrollment = self.get_object()
        self._require_owner_or_finances(enrollment)
        ser = ChargeInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        apply_cpf(enrollment.person, ser.validated_data.get("cpf", ""))
        _billing(billing.generate_charge, enrollment, ser.validated_data.get("installments"))
        return self._respond(enrollment)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        enrollment = self.get_object()
        self._require_owner_or_finances(enrollment)
        ser = CancelEnrollmentInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        is_finances = self._is_finances(enrollment)
        _billing(
            billing.cancel,
            enrollment,
            by_coordinator=is_finances,
            force=is_finances and ser.validated_data["force"],
            note=ser.validated_data.get("note", ""),
            manual_refund=is_finances and ser.validated_data["manual_refund"],
        )
        return self._respond(enrollment)

    @action(detail=True, methods=["post"])
    def scholarship(self, request, pk=None):
        enrollment = self.get_object()
        self._require_finances(enrollment)
        ser = ScholarshipInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        _billing(
            billing.grant_scholarship,
            enrollment,
            ser.validated_data["discount_cents"],
            ser.validated_data.get("reason", ""),
            request.user,
        )
        return self._respond(enrollment)

    @action(detail=True, methods=["post"], url_path="manual-payment")
    def manual_payment(self, request, pk=None):
        enrollment = self.get_object()
        self._require_finances(enrollment)
        ser = ManualPaymentInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        _billing(
            billing.record_manual_payment,
            enrollment,
            ser.validated_data["amount_cents"],
            ser.validated_data.get("note", ""),
            request.user,
        )
        return self._respond(enrollment)

    @action(detail=True, methods=["get"])
    def certificate(self, request, pk=None):
        """The completion certificate (PDF): the student, and whoever manages the class."""
        enrollment = self.get_object()
        if enrollment.person_id != request.user.pk and not can_manage_class(
            request.user, enrollment.course_class
        ):
            raise PermissionDenied()
        if enrollment.status != EnrollmentStatus.COMPLETED:
            raise drf_serializers.ValidationError(
                {"detail": "O certificado fica disponível quando o curso é concluído."}
            )
        certificates.ensure_code(enrollment)
        pdf = certificates.build_pdf(enrollment, origin=request.headers.get("Origin", ""))
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{certificates.filename(enrollment)}"'
        return response

    @action(detail=True, methods=["post"])
    def material(self, request, pk=None):
        """Marks the class material as delivered (or not) — teachers too."""
        enrollment = self.get_object()
        if not can_manage_class(request.user, enrollment.course_class):
            raise PermissionDenied()
        ser = MaterialInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if ser.validated_data["delivered"]:
            enrollment.material_delivered_at = timezone.now()
            enrollment.material_delivered_by = request.user
        else:
            enrollment.material_delivered_at = None
            enrollment.material_delivered_by = None
        enrollment.save(update_fields=["material_delivered_at", "material_delivered_by", "updated_at"])
        return Response(EnrollmentSerializer(enrollment).data)


class CertificateVerifyView(APIView):
    """Public: confirms a certificate by its code (no login), showing who
    completed which course — never contact data."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, code):
        enrollment = certificates.find_valid(code)
        if enrollment is None:
            raise Http404
        return Response(certificates.public_data(enrollment))


class ClassExpenseViewSet(viewsets.ModelViewSet):
    """A class's expenses (?course_class=<id>), for its coordinators."""

    serializer_class = ClassExpenseSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = ClassExpense.objects.select_related("course_class", "created_by")
        visible = scope_classes(CourseClass.objects.all(), self.request.user)
        qs = qs.filter(course_class__in=visible)
        course_class_id = self.request.query_params.get("course_class")
        if course_class_id:
            qs = qs.filter(course_class_id=course_class_id)
        return [e for e in qs if can_manage_class_finances(self.request.user, e.course_class)]

    def get_object(self):
        expense = get_object_or_404(ClassExpense, pk=self.kwargs["pk"])
        if not can_manage_class_finances(self.request.user, expense.course_class):
            raise PermissionDenied()
        return expense

    def perform_create(self, serializer):
        course_class = serializer.validated_data["course_class"]
        if not can_manage_class_finances(self.request.user, course_class):
            raise PermissionDenied("Só a coordenação do curso registra despesas da turma.")
        serializer.save(created_by=self.request.user)


class LessonViewSet(viewsets.ModelViewSet):
    serializer_class = LessonSerializer
    permission_classes = [LessonPermission]

    def get_queryset(self):
        # Only lessons of classes the person can see (coordinators, teachers).
        visible = scope_classes(CourseClass.objects.all(), self.request.user)
        qs = Lesson.objects.select_related("course_class").prefetch_related("attendances").filter(
            course_class__in=visible
        )
        class_id = self.request.query_params.get("course_class_id")
        if class_id:
            qs = qs.filter(course_class_id=class_id)
        return qs

    def perform_create(self, serializer):
        serializer.save()

    # ── Attendance ──────────────────────────────────────────────────────

    @action(detail=True, methods=["get", "post"], url_path="attendance")
    def attendance(self, request, pk=None):
        lesson = self.get_object()
        if request.method == "GET":
            return Response(
                LessonAttendanceSerializer(
                    lesson.attendances.select_related("person"), many=True
                ).data
            )
        if not can_manage_class(request.user, lesson.course_class):
            raise PermissionDenied()
        ser = BulkAttendanceInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        results = []
        for item in ser.validated_data["attendances"]:
            person = get_object_or_404(Person, pk=item["person_id"])
            obj, _ = LessonAttendance.objects.update_or_create(
                lesson=lesson,
                person=person,
                defaults={
                    "present": item["present"],
                    "recorded_by": request.user,
                },
            )
            results.append(obj)
        return Response(
            LessonAttendanceSerializer(results, many=True).data,
            status=status.HTTP_200_OK,
        )

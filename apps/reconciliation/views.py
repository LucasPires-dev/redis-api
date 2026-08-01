from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.churches.choices import MinistryKind
from apps.churches.models import Campus, Cell, Ministry

from .choices import LeadStatus
from .models import Reconciliation, ReconciliationLead
from .permissions import (
    ReconciliationLeadPermission,
    ReconciliationPermission,
    can_manage_reconciliation,
    can_triage_campus,
    scope_reconciliation_leads,
    scope_reconciliations,
)
from .serializers import (
    AvailableCellSerializer,
    AvailableMinistrySerializer,
    ReconciliationAssignInputSerializer,
    ReconciliationCampusMiniSerializer,
    ReconciliationCellInputSerializer,
    ReconciliationJornadaInputSerializer,
    ReconciliationLeadSerializer,
    ReconciliationLeadTriageInputSerializer,
    ReconciliationMinistryInputSerializer,
    ReconciliationPersonMiniSerializer,
    ReconciliationReassignInputSerializer,
    ReconciliationSerializer,
)

Person = get_user_model()


class ReconciliationLeadViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ReconciliationLeadSerializer
    permission_classes = [ReconciliationLeadPermission]

    def get_queryset(self):
        qs = ReconciliationLead.objects.select_related("campus")
        # The default-to-"pending" filter only applies to the list view — detail
        # lookups (retrieve/triage/discard/assign) must find a lead regardless of
        # its current status, otherwise e.g. re-POSTing `assign` on an already-
        # assigned lead 404s instead of hitting the "already processed" 400 below.
        if self.action == "list":
            status_param = self.request.query_params.get("status", LeadStatus.PENDING)
            if status_param != "all":
                qs = qs.filter(status=status_param)
        return scope_reconciliation_leads(qs, self.request.user)

    @action(detail=True, methods=["post"], url_path="triage")
    def triage(self, request, pk=None):
        lead = self.get_object()  # has_object_permission -> can_triage_lead
        serializer = ReconciliationLeadTriageInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead.campus = serializer.validated_data["campus_id"]
        lead.save(update_fields=["campus", "updated_at"])
        return Response(ReconciliationLeadSerializer(lead).data)

    @action(detail=True, methods=["post"], url_path="discard")
    def discard(self, request, pk=None):
        lead = self.get_object()
        lead.status = LeadStatus.DISCARDED
        lead.save(update_fields=["status", "updated_at"])
        return Response(ReconciliationLeadSerializer(lead).data)

    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        lead = self.get_object()
        if lead.status != LeadStatus.PENDING:
            raise ValidationError("Este lead já foi processado.")
        serializer = ReconciliationAssignInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        campus_id = serializer.validated_data.get("campus_id")
        campus = get_object_or_404(Campus, pk=campus_id) if campus_id else lead.campus
        if campus is None:
            raise ValidationError({"campus_id": "Defina o campus do lead antes de atribuir."})
        if not can_triage_campus(request.user, campus):
            raise PermissionDenied("Você não pode atribuir leads deste campus.")

        reconciliador = get_object_or_404(Person, pk=serializer.validated_data["reconciliador_id"])
        reconciliacao_ministry = Ministry.objects.filter(campus=campus, kind=MinistryKind.RECONCILIACAO).first()
        if not reconciliacao_ministry or not reconciliacao_ministry.members.filter(pk=reconciliador.pk).exists():
            raise ValidationError(
                "Só pode atribuir a alguém que serve no ministério de Reconciliação deste campus."
            )

        try:
            with transaction.atomic():
                convert = Person.objects.create_user(
                    email=lead.email or f"convert+{lead.id}@reconciliacao.placeholder",
                    name=lead.name,
                    phone=lead.phone,
                    campus=campus,
                    password=None,
                )
                reconciliation = Reconciliation.objects.create(
                    convert=convert,
                    reconciliador=reconciliador,
                    campus=campus,
                    assigned_by=request.user,
                    source_lead=lead,
                )
                lead.campus = campus
                lead.status = LeadStatus.ASSIGNED
                lead.save(update_fields=["campus", "status", "updated_at"])
        except IntegrityError:
            raise ValidationError(
                {"email": "Já existe uma pessoa cadastrada com este email — verifique duplicidade manualmente."}
            )
        return Response(ReconciliationSerializer(reconciliation).data, status=status.HTTP_201_CREATED)


class ReconciliationViewSet(viewsets.ModelViewSet):
    """A Reconciliation is only ever created via ReconciliationLeadViewSet.assign (it
    always originates from a lead + a freshly minted Person) — plain POST here is
    disabled."""

    serializer_class = ReconciliationSerializer
    permission_classes = [ReconciliationPermission]

    def get_queryset(self):
        qs = Reconciliation.objects.select_related(
            "convert", "reconciliador", "campus", "assigned_by"
        ).prefetch_related("convert__cells", "convert__ministries")
        convert_id = self.request.query_params.get("convert")
        if convert_id:
            qs = qs.filter(convert_id=convert_id)
        campus_id = self.request.query_params.get("campus")
        if campus_id:
            qs = qs.filter(campus_id=campus_id)
        return scope_reconciliations(qs, self.request.user)

    def create(self, request, *args, **kwargs):
        raise MethodNotAllowed("POST", detail="Crie via /api/reconciliation-leads/{id}/assign/.")

    @action(detail=True, methods=["patch"], url_path="jornada")
    def jornada(self, request, pk=None):
        reconciliation = self.get_object()  # can_manage_reconciliation (incl. the reconciliador)
        serializer = ReconciliationJornadaInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        completed = serializer.validated_data["jornada_completed"]
        reconciliation.jornada_completed = completed
        reconciliation.jornada_completed_at = serializer.validated_data.get("jornada_completed_at") or (
            timezone.localdate() if completed else None
        )
        reconciliation.save(update_fields=["jornada_completed", "jornada_completed_at", "updated_at"])
        return Response(ReconciliationSerializer(reconciliation).data)

    @action(detail=True, methods=["post"], url_path="reassign")
    def reassign(self, request, pk=None):
        reconciliation = self.get_object()
        # Deliberately can_triage_campus, NOT can_manage_reconciliation — a
        # reconciliador must not be able to reassign their own converts away.
        if not can_triage_campus(request.user, reconciliation.campus):
            raise PermissionDenied("Só quem lidera o campus/ministério de Reconciliação (ou o gestor) pode reatribuir.")
        serializer = ReconciliationReassignInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_reconciliador = get_object_or_404(Person, pk=serializer.validated_data["reconciliador_id"])
        reconciliacao_ministry = Ministry.objects.filter(
            campus=reconciliation.campus, kind=MinistryKind.RECONCILIACAO
        ).first()
        if not reconciliacao_ministry or not reconciliacao_ministry.members.filter(pk=new_reconciliador.pk).exists():
            raise ValidationError("Só pode reatribuir a alguém que serve no ministério de Reconciliação deste campus.")
        reconciliation.reconciliador = new_reconciliador
        reconciliation.save(update_fields=["reconciliador", "updated_at"])
        return Response(ReconciliationSerializer(reconciliation).data)

    @action(detail=True, methods=["get"], url_path="available-cells")
    def available_cells(self, request, pk=None):
        # A rank-and-file reconciliador (no leadership) has no read access to
        # /api/cells/ under scope_cells — this is the only way they can see which
        # GTs exist at their convert's campus to pick one.
        reconciliation = self.get_object()  # can_manage_reconciliation (incl. the reconciliador)
        cells = Cell.objects.filter(campus=reconciliation.campus)
        return Response(AvailableCellSerializer(cells, many=True).data)

    @action(detail=True, methods=["get"], url_path="available-ministries")
    def available_ministries(self, request, pk=None):
        reconciliation = self.get_object()
        ministries = Ministry.objects.filter(campus=reconciliation.campus)
        return Response(AvailableMinistrySerializer(ministries, many=True).data)

    @action(detail=True, methods=["post"], url_path="cell")
    def add_to_cell(self, request, pk=None):
        reconciliation = self.get_object()
        serializer = ReconciliationCellInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cell = get_object_or_404(Cell, pk=serializer.validated_data["cell_id"])
        if cell.campus_id != reconciliation.campus_id:
            raise ValidationError("A célula precisa ser do mesmo campus do convertido.")
        cell.members.add(reconciliation.convert)
        # `reconciliation` was fetched with convert__cells already prefetched (see
        # get_queryset) — that cache predates the .add() above, so re-fetching
        # before serializing avoids the response echoing a stale in_cell=false.
        reconciliation.refresh_from_db()
        return Response(ReconciliationSerializer(reconciliation).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="ministry")
    def add_to_ministry(self, request, pk=None):
        reconciliation = self.get_object()
        serializer = ReconciliationMinistryInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ministry = get_object_or_404(Ministry, pk=serializer.validated_data["ministry_id"])
        if ministry.campus_id != reconciliation.campus_id:
            raise ValidationError("O ministério precisa ser do mesmo campus do convertido.")
        ministry.members.add(reconciliation.convert)
        reconciliation.refresh_from_db()
        return Response(ReconciliationSerializer(reconciliation).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="dashboard")
    def dashboard(self, request):
        qs = scope_reconciliations(
            Reconciliation.objects.select_related("convert", "reconciliador", "campus").prefetch_related(
                "convert__cells", "convert__ministries"
            ),
            request.user,
        )
        campus_id = request.query_params.get("campus")
        if campus_id:
            qs = qs.filter(campus_id=campus_id)
        rows = list(qs)

        def bucket(rs):
            return {
                "total": len(rs),
                "baptized": sum(1 for r in rs if r.convert.baptized),
                "in_cell": sum(1 for r in rs if bool(r.convert.cells.all())),
                "serving": sum(1 for r in rs if bool(r.convert.ministries.all())),
                "jornada_completed": sum(1 for r in rs if r.jornada_completed),
            }

        by_reconciliador, by_campus = {}, {}
        for r in rows:
            by_reconciliador.setdefault(r.reconciliador_id, {"person": r.reconciliador, "rows": []})["rows"].append(r)
            by_campus.setdefault(r.campus_id, {"campus": r.campus, "rows": []})["rows"].append(r)

        return Response(
            {
                "overall": bucket(rows),
                "by_reconciliador": [
                    {"reconciliador": ReconciliationPersonMiniSerializer(d["person"]).data, **bucket(d["rows"])}
                    for d in by_reconciliador.values()
                ],
                "by_campus": [
                    {"campus": ReconciliationCampusMiniSerializer(d["campus"]).data, **bucket(d["rows"])}
                    for d in by_campus.values()
                ],
            }
        )

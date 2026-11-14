""""Quero participar" of a public cell (see CellJoinRequest).

- POST /cell-join-requests/ {cell_id, message}: any account asks to join a
  public, active cell (one pending request per cell).
- GET /cell-join-requests/mine/: the caller's requests; accepted ones carry
  the cell's address and leaders.
- GET /cell-join-requests/?cell=: the pending requests of a cell, for whoever
  manages it.
- POST /cell-join-requests/{id}/accept/ | decline/: the cell's leadership
  decides. Accepting creates the named visitor (the cell's follow-up).
- POST /cell-join-requests/{id}/cancel/: the person gives up.
"""

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.notifications import triggers

from .choices import CellRole, JoinRequestStatus, VisitorStatus
from .models import Cell, CellJoinRequest, CellVisitor
from .permissions import can_manage_cell, scope_managed_cells
from .public import public_cells


def _people(cell, role):
    return [
        {"name": lead.person.name, "phone": lead.person.phone}
        for lead in cell.leaderships.all()
        if lead.role == role
    ]


def accepted_cell(cell):
    """What an accepted person gets: where and with whom."""
    return {
        "name": cell.name,
        "address": cell.address,
        "meeting_day_display": cell.get_meeting_day_display(),
        "meeting_time": cell.meeting_time.strftime("%H:%M") if cell.meeting_time else None,
        "leaders": _people(cell, CellRole.LEADER) + _people(cell, CellRole.CO_LEADER),
    }


class CellJoinRequestSerializer(serializers.ModelSerializer):
    cell_id = serializers.PrimaryKeyRelatedField(queryset=Cell.objects.all(), source="cell", write_only=True)
    cell = serializers.SerializerMethodField()
    person = serializers.SerializerMethodField()

    class Meta:
        model = CellJoinRequest
        fields = ["id", "cell_id", "cell", "person", "message", "status", "created_at", "decided_at"]
        read_only_fields = ["status", "created_at", "decided_at"]

    def get_cell(self, obj):
        data = {"id": str(obj.cell_id), "church": obj.cell.campus.church.name}
        if obj.status == JoinRequestStatus.APPROVED and self.context.get("mine"):
            data.update(accepted_cell(obj.cell))
        return data

    def get_person(self, obj):
        # Contact data for the cell's leadership (the requests list is theirs).
        if self.context.get("mine"):
            return None
        p = obj.person
        return {"id": str(p.pk), "name": p.name, "phone": p.phone, "email": p.email}

    def validate_cell_id(self, cell):
        if not public_cells().filter(pk=cell.pk).exists():
            raise serializers.ValidationError("Esta célula não está aberta ao público.")
        return cell


class CellJoinRequestViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = CellJoinRequestSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        qs = CellJoinRequest.objects.select_related("cell__campus__church", "person").filter(
            cell__in=scope_managed_cells(Cell.objects.all(), self.request.user),
            status=JoinRequestStatus.PENDING,
        )
        cell_id = self.request.query_params.get("cell")
        return qs.filter(cell_id=cell_id) if cell_id else qs

    def perform_create(self, serializer):
        cell, person = serializer.validated_data["cell"], self.request.user
        if cell.members.filter(pk=person.pk).exists() or cell.leaderships.filter(person=person).exists():
            raise ValidationError("Você já participa desta célula.")
        if CellJoinRequest.objects.filter(cell=cell, person=person, status=JoinRequestStatus.PENDING).exists():
            raise ValidationError("Seu pedido já foi enviado. Aguarde a resposta da liderança.")
        join_request = serializer.save(person=person)
        triggers.cell_join_requested(join_request)

    @action(detail=False, methods=["get"])
    def mine(self, request):
        qs = CellJoinRequest.objects.filter(person=request.user).select_related("cell__campus__church")
        cell_id = request.query_params.get("cell")
        if cell_id:
            qs = qs.filter(cell_id=cell_id)
        qs = qs.prefetch_related("cell__leaderships__person")
        return Response(self.get_serializer(qs, many=True, context={"request": request, "mine": True}).data)

    def _pending(self, pk):
        join_request = get_object_or_404(
            CellJoinRequest.objects.select_related("cell__campus__church", "person"), pk=pk
        )
        if join_request.status != JoinRequestStatus.PENDING:
            raise ValidationError("Este pedido já foi respondido.")
        return join_request

    def _decide(self, request, pk, status):
        join_request = self._pending(pk)
        if not can_manage_cell(request.user, join_request.cell):
            raise PermissionDenied("Só a liderança da célula responde aos pedidos.")
        with transaction.atomic():
            if status == JoinRequestStatus.APPROVED:
                person = join_request.person
                join_request.visitor = CellVisitor.objects.create(
                    cell=join_request.cell,
                    name=person.name,
                    phone=person.phone,
                    email=person.email,
                    notes=join_request.message,
                    status=VisitorStatus.VISITING,
                    created_by=request.user,
                )
            join_request.status = status
            join_request.decided_at = timezone.now()
            join_request.decided_by = request.user
            join_request.save()
        triggers.cell_join_answered(join_request)
        return Response(self.get_serializer(join_request).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._decide(request, pk, JoinRequestStatus.APPROVED)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        return self._decide(request, pk, JoinRequestStatus.REJECTED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        join_request = self._pending(pk)
        if join_request.person_id != request.user.pk:
            raise PermissionDenied()
        join_request.status = JoinRequestStatus.CANCELLED
        join_request.decided_at = timezone.now()
        join_request.save(update_fields=["status", "decided_at"])
        return Response(self.get_serializer(join_request, context={"request": request, "mine": True}).data)

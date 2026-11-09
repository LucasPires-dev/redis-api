from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.churches.models import Campus, Cell, Ministry

from .models import Reconciliation, ReconciliationLead

Person = get_user_model()


class ReconciliationPersonMiniSerializer(serializers.ModelSerializer):
    """Small, local read-only view of a Person — defined here (instead of importing
    apps.people.serializers) to avoid a reconciliation<->people serializer import cycle.
    Named distinctly from apps.churches/people's own PersonMiniSerializer so
    drf-spectacular doesn't collide two differently-shaped components under the
    same schema component name."""

    class Meta:
        model = Person
        fields = ["id", "name", "email"]


class ReconciliationCampusMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campus
        fields = ["id", "name", "city"]


class ReconciliationLeadSerializer(serializers.ModelSerializer):
    campus = ReconciliationCampusMiniSerializer(read_only=True)

    class Meta:
        model = ReconciliationLead
        fields = [
            "id",
            "name",
            "phone",
            "email",
            "raw_campus_label",
            "campus",
            "submitted_at",
            "status",
            "created_at",
        ]
        read_only_fields = fields


class ReconciliationLeadTriageInputSerializer(serializers.Serializer):
    campus_id = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())


class ReconciliationAssignInputSerializer(serializers.Serializer):
    reconciliador_id = serializers.UUIDField()
    campus_id = serializers.UUIDField(required=False)


class ReconciliationSerializer(serializers.ModelSerializer):
    convert = ReconciliationPersonMiniSerializer(read_only=True)
    reconciliador = ReconciliationPersonMiniSerializer(read_only=True)
    campus = ReconciliationCampusMiniSerializer(read_only=True)
    assigned_by = ReconciliationPersonMiniSerializer(read_only=True)
    baptized = serializers.BooleanField(source="convert.baptized", read_only=True)
    baptized_at = serializers.DateField(source="convert.baptized_at", read_only=True)
    in_cell = serializers.SerializerMethodField()
    serving = serializers.SerializerMethodField()

    class Meta:
        model = Reconciliation
        fields = [
            "id",
            "convert",
            "reconciliador",
            "campus",
            "assigned_by",
            "assigned_at",
            "jornada_completed",
            "jornada_completed_at",
            "baptized",
            "baptized_at",
            "in_cell",
            "serving",
            "source_lead",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "convert",
            "campus",
            "assigned_by",
            "assigned_at",
            "source_lead",
            "created_at",
            "updated_at",
        ]

    def get_in_cell(self, obj):
        # .all() (not .exists()) reuses the prefetch cache set up by the viewset's
        # queryset instead of issuing a fresh query per row.
        return bool(obj.convert.cells.all())

    def get_serving(self, obj):
        return bool(obj.convert.ministries.all())


class ReconciliationJornadaInputSerializer(serializers.Serializer):
    jornada_completed = serializers.BooleanField()
    jornada_completed_at = serializers.DateField(required=False, allow_null=True)


class ReconciliationReassignInputSerializer(serializers.Serializer):
    reconciliador_id = serializers.UUIDField()


class ReconciliationCellInputSerializer(serializers.Serializer):
    cell_id = serializers.UUIDField()


class ReconciliationMinistryInputSerializer(serializers.Serializer):
    ministry_id = serializers.UUIDField()


class AvailableCellSerializer(serializers.ModelSerializer):
    """Cell picker option for the 'adicionar a um GT' action — deliberately not the
    same as apps.churches's CellSerializer/scope_cells: a rank-and-file
    reconciliador (a plain Ministry.members entry, no leadership) has no read
    access to /api/cells/ under the normal scoping, but must still be able to see
    every GT at their assigned convert's campus to pick one. Exposed only via
    ReconciliationViewSet.available_cells, gated by can_manage_reconciliation."""

    class Meta:
        model = Cell
        fields = ["id", "name", "meeting_day", "meeting_time"]


class AvailableMinistrySerializer(serializers.ModelSerializer):
    """Same rationale as AvailableCellSerializer, for the 'adicionar a um
    Ministério' action."""

    class Meta:
        model = Ministry
        fields = ["id", "name", "kind"]

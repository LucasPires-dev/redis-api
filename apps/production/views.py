from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .choices import DemandStatus, RosterStatus
from .models import Demand, Event, EventChecklistItem, EventRosterEntry, RundownItem, RundownItemStatus
from .permissions import (
    PRODUCER_EVENT_TYPES,
    DemandPermission,
    EventChecklistItemPermission,
    EventPermission,
    EventRosterEntryPermission,
    RundownItemPermission,
    is_any_event_producer,
    scope_demands,
    scope_events,
)
from .realtime import broadcast_event_update
from .serializers import (
    DemandSerializer,
    EventChecklistItemSerializer,
    EventRosterEntrySerializer,
    EventSerializer,
    RejectDemandInputSerializer,
    RundownItemSerializer,
    SetItemStatusInputSerializer,
)


class EventViewSet(viewsets.ModelViewSet):
    serializer_class = EventSerializer
    permission_classes = [EventPermission]

    def get_queryset(self):
        qs = Event.objects.select_related("campus").prefetch_related("rundown_items__statuses")
        campus_id = self.request.query_params.get("campus")
        if campus_id:
            qs = qs.filter(campus_id=campus_id)
        return scope_events(qs, self.request.user)

    def perform_create(self, serializer):
        extra = {"created_by": self.request.user}
        event_type = serializer.validated_data.get("event_type")
        if event_type in PRODUCER_EVENT_TYPES and is_any_event_producer(self.request.user):
            extra["producer"] = self.request.user
        serializer.save(**extra)


class RundownItemViewSet(viewsets.ModelViewSet):
    serializer_class = RundownItemSerializer
    permission_classes = [RundownItemPermission]

    def get_queryset(self):
        qs = RundownItem.objects.select_related("event", "event__campus").prefetch_related("statuses")
        event_id = self.request.query_params.get("event")
        if event_id:
            qs = qs.filter(event_id=event_id)
        return qs.filter(event__in=scope_events(Event.objects.all(), self.request.user))

    def perform_create(self, serializer):
        item = serializer.save()
        broadcast_event_update(item.event_id, "rundown_item")

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        item = self.get_object()  # has_object_permission -> can_report_item_status
        serializer = SetItemStatusInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        RundownItemStatus.objects.update_or_create(
            rundown_item=item,
            ministry_kind=serializer.validated_data["ministry_kind"],
            defaults={
                "status": serializer.validated_data["status"],
                "updated_by": request.user,
            },
        )
        item.refresh_from_db()
        broadcast_event_update(item.event_id, "rundown_item_status")
        return Response(RundownItemSerializer(item).data)


class DemandViewSet(viewsets.ModelViewSet):
    serializer_class = DemandSerializer
    permission_classes = [DemandPermission]

    def get_queryset(self):
        qs = Demand.objects.select_related("event", "event__campus", "from_campus", "to_campus", "created_by")
        direction = self.request.query_params.get("direction")
        event_id = self.request.query_params.get("event")
        if event_id:
            qs = qs.filter(event_id=event_id)
        return scope_demands(qs, self.request.user, direction=direction)

    def perform_create(self, serializer):
        demand = serializer.save(created_by=self.request.user, status=DemandStatus.OPEN)
        if demand.event_id:
            broadcast_event_update(demand.event_id, "demand")

    @action(detail=True, methods=["post"], url_path="accept")
    def accept(self, request, pk=None):
        demand = self.get_object()  # has_object_permission -> can_manage_demand_target
        demand.status = DemandStatus.IN_PROGRESS
        demand.save(update_fields=["status", "updated_at"])
        if demand.event_id:
            broadcast_event_update(demand.event_id, "demand")
        return Response(DemandSerializer(demand).data)

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        demand = self.get_object()
        demand.status = DemandStatus.DONE
        demand.save(update_fields=["status", "updated_at"])
        if demand.event_id:
            broadcast_event_update(demand.event_id, "demand")
        return Response(DemandSerializer(demand).data)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        demand = self.get_object()
        serializer = RejectDemandInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demand.status = DemandStatus.REJECTED
        demand.rejection_reason = serializer.validated_data["rejection_reason"]
        demand.save(update_fields=["status", "rejection_reason", "updated_at"])
        if demand.event_id:
            broadcast_event_update(demand.event_id, "demand")
        return Response(DemandSerializer(demand).data)


class EventRosterEntryViewSet(viewsets.ModelViewSet):
    serializer_class = EventRosterEntrySerializer
    permission_classes = [EventRosterEntryPermission]

    def get_queryset(self):
        qs = EventRosterEntry.objects.select_related("ministry", "person", "event")
        event_id = self.request.query_params.get("event")
        if event_id:
            qs = qs.filter(event_id=event_id)
        return qs.filter(event__in=scope_events(Event.objects.all(), self.request.user))

    def perform_create(self, serializer):
        entry = serializer.save(invited_by=self.request.user)
        broadcast_event_update(entry.event_id, "roster_entry")

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, pk=None):
        entry = self.get_object()  # has_object_permission -> can_respond_roster_entry
        entry.status = RosterStatus.CONFIRMED
        entry.responded_at = timezone.now()
        entry.save(update_fields=["status", "responded_at"])
        broadcast_event_update(entry.event_id, "roster_entry")
        return Response(EventRosterEntrySerializer(entry).data)

    @action(detail=True, methods=["post"], url_path="decline")
    def decline(self, request, pk=None):
        entry = self.get_object()
        entry.status = RosterStatus.DECLINED
        entry.responded_at = timezone.now()
        entry.save(update_fields=["status", "responded_at"])
        broadcast_event_update(entry.event_id, "roster_entry")
        return Response(EventRosterEntrySerializer(entry).data)


class EventChecklistItemViewSet(viewsets.ModelViewSet):
    serializer_class = EventChecklistItemSerializer
    permission_classes = [EventChecklistItemPermission]

    def get_queryset(self):
        qs = EventChecklistItem.objects.select_related("event", "done_by")
        event_id = self.request.query_params.get("event")
        if event_id:
            qs = qs.filter(event_id=event_id)
        return qs.filter(event__in=scope_events(Event.objects.all(), self.request.user))

    def perform_create(self, serializer):
        item = serializer.save(created_by=self.request.user)
        broadcast_event_update(item.event_id, "checklist_item")

    @action(detail=True, methods=["post"], url_path="toggle")
    def toggle(self, request, pk=None):
        item = self.get_object()  # has_object_permission -> can_manage_checklist_item
        item.is_done = not item.is_done
        item.done_by = request.user if item.is_done else None
        item.done_at = timezone.now() if item.is_done else None
        item.save(update_fields=["is_done", "done_by", "done_at"])
        broadcast_event_update(item.event_id, "checklist_item")
        return Response(EventChecklistItemSerializer(item).data)

from datetime import timedelta

from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.churches.models import Ministry
from apps.churches.permissions import can_manage_ministry
from apps.notifications import triggers

from . import roster, series
from .choices import DemandStatus, RosterStatus, SwapStatus
from .models import (
    Demand,
    Event,
    EventChecklistItem,
    EventRosterEntry,
    EventSeries,
    RosterSwapRequest,
    RundownItem,
    RundownItemStatus,
)
from .permissions import (
    PRODUCER_EVENT_TYPES,
    DemandPermission,
    EventChecklistItemPermission,
    EventPermission,
    EventRosterEntryPermission,
    EventSeriesPermission,
    RundownItemPermission,
    is_any_event_producer,
    can_manage_event,
    can_manage_series,
    scope_demands,
    scope_events,
)
from .realtime import broadcast_event_update
from .serializers import (
    DemandSerializer,
    DuplicateEventInputSerializer,
    EventChecklistItemSerializer,
    EventRosterEntrySerializer,
    EventSerializer,
    EventSeriesSerializer,
    RejectDemandInputSerializer,
    RosterSwapSerializer,
    RundownItemSerializer,
    SetItemStatusInputSerializer,
    SwapInputSerializer,
)


class EventPagination(PageNumberPagination):
    """The calendar loads a whole month at once (?page_size=)."""

    page_size_query_param = "page_size"
    max_page_size = 500


class EventViewSet(viewsets.ModelViewSet):
    serializer_class = EventSerializer
    permission_classes = [EventPermission]
    pagination_class = EventPagination

    def get_queryset(self):
        qs = Event.objects.select_related("campus", "ticket_config").prefetch_related(
            "rundown_items__statuses"
        )
        params = self.request.query_params
        campus_id = params.get("campus")
        if campus_id:
            qs = qs.filter(campus_id=campus_id)
        # The calendar asks for a range of days: ?from=2026-11-01&to=2026-11-30
        if params.get("from"):
            qs = qs.filter(starts_at__date__gte=params["from"])
        if params.get("to"):
            qs = qs.filter(starts_at__date__lte=params["to"])
        return scope_events(qs, self.request.user)

    def perform_destroy(self, instance):
        series.skip(instance)  # a deleted occurrence of a recurring event stays deleted
        instance.delete()

    @action(detail=True, methods=["post"])
    def duplicate(self, request, pk=None):
        """A copy on another date: rundown, checklist, ticket and (optionally) the roster."""
        source = self.get_object()  # has_object_permission -> can_manage_event
        data = DuplicateEventInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        event = series.duplicate_event(
            source,
            data.validated_data["starts_at"],
            by=request.user,
            title=data.validated_data["title"].strip() or None,
            copy_roster=data.validated_data["copy_roster"],
        )
        return Response(EventSerializer(event, context={"request": request}).data, status=201)

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
        triggers.demand_received(demand)
        if demand.event_id:
            broadcast_event_update(demand.event_id, "demand")

    @action(detail=True, methods=["post"], url_path="accept")
    def accept(self, request, pk=None):
        demand = self.get_object()  # has_object_permission -> can_manage_demand_target
        demand.status = DemandStatus.IN_PROGRESS
        demand.save(update_fields=["status", "updated_at"])
        triggers.demand_answered(demand, request.user)
        if demand.event_id:
            broadcast_event_update(demand.event_id, "demand")
        return Response(DemandSerializer(demand).data)

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        demand = self.get_object()
        demand.status = DemandStatus.DONE
        demand.save(update_fields=["status", "updated_at"])
        triggers.demand_answered(demand, request.user)
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
        triggers.demand_answered(demand, request.user)
        if demand.event_id:
            broadcast_event_update(demand.event_id, "demand")
        return Response(DemandSerializer(demand).data)


class EventRosterEntryViewSet(viewsets.ModelViewSet):
    serializer_class = EventRosterEntrySerializer
    permission_classes = [EventRosterEntryPermission]

    def get_queryset(self):
        qs = EventRosterEntry.objects.select_related("ministry", "person", "event", "event__campus").prefetch_related(
            Prefetch(
                "swap_requests",
                queryset=RosterSwapRequest.objects.filter(status=SwapStatus.PENDING).select_related("substitute"),
            )
        )
        event_id = self.request.query_params.get("event")
        if event_id:
            qs = qs.filter(event_id=event_id)
        user = self.request.user
        # The person always sees their own entries (to answer them).
        return qs.filter(Q(event__in=scope_events(Event.objects.all(), user)) | Q(person=user)).distinct()

    def perform_create(self, serializer):
        entry = serializer.save(invited_by=self.request.user)
        triggers.roster_invited(entry)
        broadcast_event_update(entry.event_id, "roster_entry")

    @action(detail=False, methods=["get"])
    def mine(self, request):
        """Where I serve: upcoming (and just started) events, soonest first."""
        since = timezone.now() - timedelta(hours=6)
        entries = self.get_queryset().filter(person=request.user, event__starts_at__gte=since)
        return Response(EventRosterEntrySerializer(entries.order_by("event__starts_at"), many=True).data)

    @action(detail=False, methods=["get"])
    def conflicts(self, request):
        """Before inviting: is the person already serving at the same time?"""
        event = get_object_or_404(Event, pk=request.query_params.get("event"))
        ministry = get_object_or_404(Ministry, pk=request.query_params.get("ministry"))
        if not can_manage_ministry(request.user, ministry):
            raise PermissionDenied("Só a liderança do ministério convida para a escala.")
        found = roster.conflicts(request.query_params.get("person"), event)
        return Response([{"id": e.pk, "title": e.title, "starts_at": e.starts_at} for e in found])

    @action(detail=True, methods=["get"], url_path="swap-candidates")
    def swap_candidates(self, request, pk=None):
        entry = self.get_object()
        if entry.person_id != request.user.pk and not can_manage_ministry(request.user, entry.ministry):
            raise PermissionDenied()
        people = roster.swap_candidates(entry)
        return Response(
            [{"id": p.pk, "name": p.name, "conflict": bool(roster.conflicts(p.pk, entry.event))} for p in people]
        )

    @action(detail=True, methods=["post"])
    def swap(self, request, pk=None):
        entry = self.get_object()  # has_object_permission -> only the person on the entry
        data = SwapInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        swap = roster.request_swap(
            entry, request.user, data.validated_data["substitute_id"], data.validated_data["message"]
        )
        return Response(RosterSwapSerializer(swap).data, status=201)

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
        entry.swap_requests.filter(status=SwapStatus.PENDING).update(
            status=SwapStatus.CANCELLED, responded_at=entry.responded_at
        )
        triggers.roster_declined(entry, request.user)
        broadcast_event_update(entry.event_id, "roster_entry")
        return Response(EventRosterEntrySerializer(entry).data)


class RosterSwapViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Swap requests I made or that were made to me: pending ones by default,
    every one with ?status=all."""

    serializer_class = RosterSwapSerializer

    def get_queryset(self):
        user = self.request.user
        qs = RosterSwapRequest.objects.filter(Q(requested_by=user) | Q(substitute=user)).select_related(
            "entry__event__campus", "entry__ministry", "requested_by", "substitute"
        )
        if self.request.query_params.get("status", "pending") != "all":
            qs = qs.filter(status=SwapStatus.PENDING)
        return qs

    def _answer(self, request, fn, who):
        swap = self.get_object()
        if getattr(swap, f"{who}_id") != request.user.pk:
            raise PermissionDenied()
        fn(swap)
        swap.refresh_from_db()
        broadcast_event_update(swap.entry.event_id, "roster_entry")
        return Response(RosterSwapSerializer(swap).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._answer(request, roster.accept_swap, "substitute")

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        return self._answer(request, roster.decline_swap, "substitute")

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._answer(request, roster.cancel_swap, "requested_by")


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


class EventSeriesViewSet(viewsets.ModelViewSet):
    """Recurring events. Creating one generates its first occurrences; ending
    it (DELETE) removes the upcoming ones without registrations."""

    serializer_class = EventSeriesSerializer
    permission_classes = [EventSeriesPermission]

    def get_queryset(self):
        qs = EventSeries.objects.select_related("campus", "template", "template__campus")
        user = self.request.user
        if user.is_manager:
            return qs
        return qs.filter(pk__in=[s.pk for s in qs if can_manage_series(user, s)])

    def _check_template(self, serializer):
        template = serializer.validated_data.get("template")
        if template is not None and not can_manage_event(self.request.user, template):
            raise PermissionDenied("Você não organiza o evento escolhido como modelo.")

    def perform_create(self, serializer):
        self._check_template(serializer)
        created = serializer.save(created_by=self.request.user)
        series.generate(created)

    def perform_update(self, serializer):
        self._check_template(serializer)
        updated = serializer.save()
        series.generate(updated)

    def perform_destroy(self, instance):
        series.remove_future(instance)
        instance.delete()

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        created = series.generate(self.get_object())
        return Response({"created": len(created)})

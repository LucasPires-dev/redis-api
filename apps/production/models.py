import uuid

from django.conf import settings
from django.db import models

from apps.churches.choices import MinistryKind

from .choices import (
    DemandPriority,
    DemandStatus,
    DemandType,
    EventStatus,
    EventType,
    ItemStatus,
    RosterStatus,
    SwapStatus,
)


class Event(models.Model):
    """Generalization of "Culto": both the regular weekly service (created by the
    Coordenação Geral, with null campus and ministry_kind) and an event of an
    audience-facing ministry (Retiro de Jovens, Encontro de Mulheres...). Null
    campus = general scope (the ministry's general leader, applies to every
    campus); campus set = local scope (that campus's local leader)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.CULTO)
    campus = models.ForeignKey(
        "churches.Campus", null=True, blank=True, on_delete=models.CASCADE, related_name="production_events"
    )
    ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices, null=True, blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=EventStatus.choices, default=EventStatus.PLANNING)
    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="produced_events"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at"]

    def __str__(self):
        return f"{self.title} ({self.starts_at:%d/%m/%Y %H:%M})"


class RundownItem(models.Model):
    """One item in the service/event running order, with one main ministry in
    charge. `order` is the position in the list (it doesn't have to match
    scheduled_time — allows reordering without recalculating times)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="rundown_items")
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    scheduled_time = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["event", "order"], name="unique_order_per_event"),
        ]

    def __str__(self):
        return f"{self.title} ({self.event.title})"


class RundownItemStatus(models.Model):
    """A ministry's 🟢🟡🔴 signal for a specific rundown item — it doesn't have to
    be the item's main ministry_kind: any involved ministry can report its own
    status (e.g. Luz reports status on the "Louvor 1" item even though Louvor is
    the main one in charge)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rundown_item = models.ForeignKey(RundownItem, on_delete=models.CASCADE, related_name="statuses")
    ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices)
    status = models.CharField(max_length=16, choices=ItemStatus.choices, default=ItemStatus.OK)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rundown_item", "ministry_kind"], name="unique_status_per_ministry"
            ),
        ]

    def __str__(self):
        return f"{self.rundown_item} — {self.ministry_kind}: {self.get_status_display()}"


class Demand(models.Model):
    """A request from one ministry to another (e.g. Jovens asks Mídia to prepare
    the Retiro slides). `event` is optional — a demand can be "general", with no
    specific service/event. from_campus/to_campus record each side's campus at
    creation time (useful when the requester is a general leader with no fixed
    campus of their own)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, null=True, blank=True, on_delete=models.CASCADE, related_name="demands")
    from_ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices, null=True, blank=True)
    from_campus = models.ForeignKey(
        "churches.Campus", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    to_ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices)
    to_campus = models.ForeignKey(
        "churches.Campus", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    demand_type = models.CharField(max_length=32, choices=DemandType.choices)
    priority = models.CharField(max_length=16, choices=DemandPriority.choices, default=DemandPriority.NORMAL)
    description = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=DemandStatus.choices, default=DemandStatus.OPEN)
    rejection_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.from_ministry_kind} → {self.to_ministry_kind}: {self.demand_type} ({self.get_status_display()})"


class EventRosterEntry(models.Model):
    """Who serves at an Event. Links to a specific `Ministry` (campus-bound), not
    just a `ministry_kind` — the invite comes from that Ministry's member list
    (Ministry.members), same as the apps.reconciliation picker."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="roster_entries")
    ministry = models.ForeignKey("churches.Ministry", on_delete=models.CASCADE, related_name="+")
    person = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    function = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=16, choices=RosterStatus.choices, default=RosterStatus.INVITED)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "ministry", "person"], name="unique_roster_entry"),
        ]

    def __str__(self):
        return f"{self.person} — {self.ministry} ({self.get_status_display()})"


class RosterSwapRequest(models.Model):
    """Someone on the roster who can't serve asks another member of the same
    ministry to take their place. When the substitute accepts, the roster entry
    passes to them (confirmed); nobody has to redo the invitation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(EventRosterEntry, on_delete=models.CASCADE, related_name="swap_requests")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    substitute = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    message = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=16, choices=SwapStatus.choices, default=SwapStatus.PENDING)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["entry"],
                condition=models.Q(status="pending"),
                name="one_pending_swap_per_entry",
            ),
        ]

    def __str__(self):
        return f"{self.requested_by} → {self.substitute} ({self.get_status_display()})"


class EventChecklistItem(models.Model):
    """Pre-event checklist item, ad hoc per ministry — no templates for now
    (Phase 3/4)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="checklist_items")
    ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices)
    text = models.CharField(max_length=255)
    is_done = models.BooleanField(default=False)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    done_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.text} ({self.get_ministry_kind_display()})"


class EventProducerAssignment(models.Model):
    """Event Producer assignment. campus=null → church-wide (every campus);
    campus set → produces only at that campus."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_producer_assignments"
    )
    campus = models.ForeignKey(
        "churches.Campus", null=True, blank=True, on_delete=models.CASCADE, related_name="event_producers"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["person", "campus"], name="unique_event_producer"),
        ]

    def __str__(self):
        scope = self.campus.name if self.campus else "Church-wide"
        return f"{self.person} — Produtor ({scope})"

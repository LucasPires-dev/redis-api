import uuid

from django.conf import settings
from django.db import models

from apps.churches.choices import MinistryKind, Visibility

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


class EventSeries(models.Model):
    """A recurring event (the Sunday service, the Wednesday prayer...): its
    occurrences are created ahead of time, `weeks_ahead` weeks at a time, by a
    daily task (see series.py). With a `template`, each one is a copy of it:
    rundown, checklist, ticket and, when `copy_roster`, the roster.

    `skipped_dates` are dates whose occurrence was deleted on purpose (a
    holiday): they are not created again."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.CULTO)
    campus = models.ForeignKey(
        "churches.Campus", null=True, blank=True, on_delete=models.CASCADE, related_name="event_series"
    )
    ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices, null=True, blank=True)
    # Python's weekday(): 0 = Monday … 6 = Sunday.
    weekday = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    duration_minutes = models.PositiveIntegerField(default=120)
    every_weeks = models.PositiveSmallIntegerField(default=1)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    template = models.ForeignKey(
        "Event", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    copy_roster = models.BooleanField(default=False)
    weeks_ahead = models.PositiveSmallIntegerField(default=6)
    skipped_dates = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["weekday", "start_time"]
        verbose_name_plural = "event series"

    def __str__(self):
        return f"{self.title} (toda {self.get_weekday_display()})"

    WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

    def get_weekday_display(self):
        return self.WEEKDAYS[self.weekday]


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
    # The church it belongs to: the campus's, or (a general event) its
    # creator's. Set in save(); a general event is general within its church.
    church = models.ForeignKey(
        "churches.Church", null=True, blank=True, on_delete=models.CASCADE, related_name="events"
    )
    # Members only, or open to everyone with a public page.
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.MEMBERS)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=EventStatus.choices, default=EventStatus.PLANNING)
    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="produced_events"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    # The recurring event it was created from, if any.
    series = models.ForeignKey(
        EventSeries, null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    # People counted in the room, for events without registration (a Sunday
    # service). With registration, the check-ins are the attendance.
    headcount = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at"]

    def __str__(self):
        return f"{self.title} ({self.starts_at:%d/%m/%Y %H:%M})"

    def save(self, *args, **kwargs):
        church_id = self.resolve_church_id()
        if church_id and church_id != self.church_id:
            self.church_id = church_id
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "church"}
        super().save(*args, **kwargs)

    def resolve_church_id(self):
        """The campus's church; for a general event, the one already set, else
        the creator's (or the only church there is)."""
        from apps.churches.models import Campus, Church

        if self.campus_id:
            return Campus.objects.filter(pk=self.campus_id).values_list("church_id", flat=True).first()
        if self.church_id:
            return self.church_id
        creator = getattr(self, "created_by", None)
        if creator is not None and creator.campus_id:
            return creator.campus.church_id
        churches = list(Church.objects.values_list("pk", flat=True)[:2])
        return churches[0] if len(churches) == 1 else None


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


class ServeUnavailability(models.Model):
    """Days someone can't serve (a trip, a shift, rest), marked by the person.
    The leadership sees it when building the roster — a warning, not a block."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="unavailabilities")
    starts_on = models.DateField()
    ends_on = models.DateField()
    reason = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_on", "ends_on"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__gte=models.F("starts_on")), name="unavailability_ends_after_start"
            ),
        ]

    def __str__(self):
        return f"{self.person} indisponível de {self.starts_on} a {self.ends_on}"

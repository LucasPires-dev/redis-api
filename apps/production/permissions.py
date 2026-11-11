from django.db.models import F, Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.churches.choices import PRODUCTION_MINISTRY_KINDS
from apps.churches.models import Ministry
from apps.churches.permissions import can_manage_campus, can_manage_ministry, general_led_kinds

from .choices import EventType


PRODUCER_EVENT_TYPES = {EventType.CONFERENCE, EventType.SPECIAL_SERVICE, EventType.TRAINING}


def is_event_producer(person, campus=None) -> bool:
    """True if person produces events at `campus`. Church-wide assignments
    (campus=null) cover any campus. A church-wide event (campus=None) belongs
    to the church-wide producers only — a campus producer doesn't produce it."""
    from .models import EventProducerAssignment

    qs = EventProducerAssignment.objects.filter(person=person)
    if campus is None:
        return qs.filter(campus__isnull=True).exists()
    return qs.filter(Q(campus=campus) | Q(campus__isnull=True)).exists()


def is_general_event_producer(person) -> bool:
    """Church-wide producer (assignment with campus=null): produces on every campus."""
    from .models import EventProducerAssignment

    if not person.is_authenticated:
        return False
    return EventProducerAssignment.objects.filter(person=person, campus__isnull=True).exists()


def can_assign_campus_event_producer(person, campus) -> bool:
    """Who appoints a campus's event producers: the levels above — the manager,
    the campus leader, or a church-wide (general) producer. A campus producer
    doesn't appoint peers."""
    if not person.is_authenticated:
        return False
    return person.is_manager or can_manage_campus(person, campus) or is_general_event_producer(person)


def is_any_event_producer(person) -> bool:
    from .models import EventProducerAssignment

    return EventProducerAssignment.objects.filter(person=person).exists()


def producer_campuses(person):
    from .models import EventProducerAssignment

    assignments = EventProducerAssignment.objects.filter(person=person).select_related("campus")
    return assignments


def _local_ministry_leader(person, kind, campus) -> bool:
    if not kind or not campus:
        return False
    return Ministry.objects.filter(campus=campus, kind=kind, leaderships__person=person).exists()


def _ministry_participant(person, kind, campus) -> bool:
    """Manager-style check for "does this person represent this (kind, campus)":
    general leader of the kind, local leader at that campus, or plain member
    (read/report access, e.g. reporting a rundown item status)."""
    if not kind:
        return False
    if kind in general_led_kinds(person):
        return True
    if not campus:
        return False
    if _local_ministry_leader(person, kind, campus):
        return True
    return Ministry.objects.filter(campus=campus, kind=kind, members=person).exists()


def can_manage_event(person, event) -> bool:
    """Who may edit the Event itself (title, time, rundown items, overall
    status): manager, the event's producer, a producer at the event's campus,
    the campus leader (local event), the general leader of the owning ministry
    (any campus), or the local leader of the owning ministry at that campus."""
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    if event.producer_id and event.producer_id == person.pk:
        return True
    if is_event_producer(person, event.campus):
        return True
    if event.campus_id and can_manage_campus(person, event.campus):
        return True
    if event.ministry_kind and event.ministry_kind in general_led_kinds(person):
        return True
    return _local_ministry_leader(person, event.ministry_kind, event.campus)


def can_create_event(person, event_type, ministry_kind, campus) -> bool:
    """Who may create an Event:
    - conference/special_service/training: manager or an Event Producer at the campus
    - regular service (no ministry_kind): manager or the campus leader
    - ministry_event: leadership (general or local) of the ministry_kind
    """
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    if event_type in PRODUCER_EVENT_TYPES:
        return is_event_producer(person, campus)
    if not ministry_kind:
        return bool(campus) and can_manage_campus(person, campus)
    return ministry_kind in general_led_kinds(person) or _local_ministry_leader(person, ministry_kind, campus)


def _can_act_for_ministry_on_event(person, event, ministry_kind) -> bool:
    """Shared rule: manager, whoever manages the event, or any participant
    (leader or member, general or local) of the given ministry_kind. Used both
    for the 🟢🟡🔴 rundown item status and for the checklist."""
    if not person.is_authenticated:
        return False
    if person.is_manager or can_manage_event(person, event):
        return True
    return _ministry_participant(person, ministry_kind, event.campus)


def can_report_item_status(person, item, ministry_kind) -> bool:
    return _can_act_for_ministry_on_event(person, item.event, ministry_kind)


def can_manage_checklist_item(person, item, ministry_kind=None) -> bool:
    return _can_act_for_ministry_on_event(person, item.event, ministry_kind or item.ministry_kind)


def can_respond_roster_entry(person, entry) -> bool:
    """Confirm/decline: the invited person themselves, or whoever manages the
    ministry (local/general leader or campus leader, via can_manage_ministry)."""
    if not person.is_authenticated:
        return False
    return person.is_manager or entry.person_id == person.pk or can_manage_ministry(person, entry.ministry)


def can_manage_demand_source(person, demand) -> bool:
    """The side that opened the demand — may edit/cancel it while still open.
    When from_ministry_kind is null the demand was created by a producer from
    an event, so check event management instead."""
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    if not demand.from_ministry_kind:
        return demand.event_id is not None and can_manage_event(person, demand.event)
    return _ministry_participant(person, demand.from_ministry_kind, demand.from_campus)


def can_manage_demand_target(person, demand) -> bool:
    """The side that received the demand — may accept/complete/reject it."""
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    return _ministry_participant(person, demand.to_ministry_kind, demand.to_campus)


def scope_events(qs, person):
    """Visible events: everything for the manager; the general regular service
    (no campus/kind) is visible to anyone authenticated; any other event, to whoever
    created it, produced it, leads the campus, leads (generally or locally) the
    owning ministry, or is a member of the owning ministry. Production teams
    (media, sound, …) see their campus's events. Events open for registration
    are visible to everyone at their campus. Campus producers see every event
    at their campuses; church-wide producers, every event."""
    if person.is_manager or is_general_event_producer(person):
        # A church-wide producer produces on every campus, so sees every event.
        return qs
    kinds = general_led_kinds(person)
    condition = (
        Q(created_by=person)
        | Q(producer=person)
        | Q(campus__isnull=True, ministry_kind__isnull=True)
        | Q(campus__leaderships__person=person)
        | Q(campus__ministries__kind=F("ministry_kind"), campus__ministries__leaderships__person=person)
        | Q(campus__ministries__kind=F("ministry_kind"), campus__ministries__members=person)
        | Q(
            campus__ministries__kind__in=PRODUCTION_MINISTRY_KINDS,
            campus__ministries__leaderships__person=person,
        )
        | Q(campus__ministries__kind__in=PRODUCTION_MINISTRY_KINDS, campus__ministries__members=person)
    )
    if kinds:
        condition |= Q(ministry_kind__in=kinds)
    # Events open for registration are for the whole campus: its members must
    # find them to sign up (a paid conference at their campus, for instance).
    if person.campus_id:
        condition |= Q(campus_id=person.campus_id, ticket_config__isnull=False)

    from .models import EventProducerAssignment

    # Campus producers see every event at their campuses (church-wide ones
    # were handled above).
    campus_ids = list(
        EventProducerAssignment.objects.filter(person=person, campus__isnull=False).values_list("campus_id", flat=True)
    )
    if campus_ids:
        condition |= Q(campus_id__in=campus_ids)

    return qs.filter(condition).distinct()


def _demand_side_condition(prefix: str, person) -> Q:
    """Builds the Q for "this person represents the `prefix` side ('from'/'to') of
    this Demand": campus leader on that side, leader (general or local) or member
    of the ministry on that side. F() compares against the row's own
    *_ministry_kind field, since the kind varies per Demand (unlike reconciliation,
    which compares against a fixed constant)."""
    kind_field, campus_field = f"{prefix}_ministry_kind", f"{prefix}_campus"
    condition = Q(**{f"{campus_field}__leaderships__person": person})
    condition |= Q(**{f"{campus_field}__ministries__kind": F(kind_field), f"{campus_field}__ministries__leaderships__person": person})
    condition |= Q(**{f"{campus_field}__ministries__kind": F(kind_field), f"{campus_field}__ministries__members": person})
    kinds = general_led_kinds(person)
    if kinds:
        condition |= Q(**{f"{kind_field}__in": kinds})
    return condition


def _producer_demand_condition(person) -> Q:
    """Demands originated from an event the person can produce (from_ministry_kind
    is null). We check the event's producer field and producer assignments."""
    from .models import EventProducerAssignment

    condition = Q(from_ministry_kind__isnull=True, event__producer=person)
    assignments = EventProducerAssignment.objects.filter(person=person)
    if assignments.filter(campus__isnull=True).exists():
        condition |= Q(from_ministry_kind__isnull=True, event__isnull=False)
    else:
        campus_ids = list(assignments.values_list("campus_id", flat=True))
        if campus_ids:
            condition |= Q(from_ministry_kind__isnull=True, event__campus_id__in=campus_ids)
    return condition


def scope_demands(qs, person, direction=None):
    """Visible demands: everything for the manager. Without `direction`: whoever
    created it or represents either side. `direction="sent"`: only the source
    side (+ the creator). `direction="received"`: only the target side.
    Producer-originated demands (from_ministry_kind=null) are visible to whoever
    manages the event."""
    if person.is_manager:
        return qs
    producer_q = _producer_demand_condition(person)
    if direction == "sent":
        condition = _demand_side_condition("from", person) | Q(created_by=person) | producer_q
    elif direction == "received":
        condition = _demand_side_condition("to", person)
    else:
        condition = _demand_side_condition("from", person) | _demand_side_condition("to", person) | Q(created_by=person) | producer_q
    return qs.filter(condition).distinct()


class EventPermission(BasePermission):
    """Read: whoever can see it (queryset already filtered by scope_events).
    Create: validates the body's ministry_kind/campus_id via can_create_event.
    Editing the Event and the rundown items/status: gated in the view
    (RundownItemPermission)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            event_type = request.data.get("event_type") or EventType.CULTO
            ministry_kind = request.data.get("ministry_kind") or None
            campus_id = request.data.get("campus_id") or None
            from apps.churches.models import Campus

            campus = Campus.objects.filter(pk=campus_id).first() if campus_id else None
            return can_create_event(request.user, event_type, ministry_kind, campus)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_event(request.user, obj)


class RundownItemPermission(BasePermission):
    """Create/edit/remove an item: whoever manages the event. Setting status
    (`set_status`): any participant of the ministry given in the body."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            from .models import Event

            event_id = request.data.get("event_id")
            event = Event.objects.filter(pk=event_id).first() if event_id else None
            if event is None:
                return True  # let the serializer validation produce a clean 400
            return can_manage_event(request.user, event)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if view.action == "set_status":
            ministry_kind = request.data.get("ministry_kind")
            return can_report_item_status(request.user, obj, ministry_kind)
        return can_manage_event(request.user, obj.event)


class DemandPermission(BasePermission):
    """Create: whoever represents the source ministry given in the body. Edit
    (source side, while open): can_manage_demand_source. Accept/complete/reject
    (target side): can_manage_demand_target, checked in the view action itself
    (not here, since the HTTP method is the same POST for different actions)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            from_kind = request.data.get("from_ministry_kind")
            if not from_kind:
                from .models import Event

                event_id = request.data.get("event_id")
                event = Event.objects.filter(pk=event_id).first() if event_id else None
                if event is None:
                    return True  # let the serializer validation produce a clean 400
                return can_manage_event(request.user, event)
            from apps.churches.models import Campus

            from_campus_id = request.data.get("from_campus_id")
            from_campus = Campus.objects.filter(pk=from_campus_id).first() if from_campus_id else None
            return _ministry_participant(request.user, from_kind, from_campus) or request.user.is_manager
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if view.action in ("accept", "complete", "reject"):
            return can_manage_demand_target(request.user, obj)
        return can_manage_demand_source(request.user, obj)


class EventRosterEntryPermission(BasePermission):
    """Invite (create): whoever manages the ministry given in the body.
    Confirm/decline: can_respond_roster_entry (the invitee themselves or whoever
    manages the ministry). Remove: whoever manages the ministry."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            ministry_id = request.data.get("ministry_id")
            ministry = Ministry.objects.filter(pk=ministry_id).first() if ministry_id else None
            if ministry is None:
                return True  # let the serializer validation produce a clean 400
            return can_manage_ministry(request.user, ministry)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if view.action in ("confirm", "decline"):
            return can_respond_roster_entry(request.user, obj)
        return can_manage_ministry(request.user, obj.ministry)


class EventChecklistItemPermission(BasePermission):
    """Create/mark done: whoever may act for the ministry_kind given in the body
    on that event (_can_act_for_ministry_on_event)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            from .models import Event

            event_id = request.data.get("event_id")
            ministry_kind = request.data.get("ministry_kind")
            event = Event.objects.filter(pk=event_id).first() if event_id else None
            if event is None or not ministry_kind:
                return True  # let the serializer validation produce a clean 400
            return _can_act_for_ministry_on_event(request.user, event, ministry_kind)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_checklist_item(request.user, obj)

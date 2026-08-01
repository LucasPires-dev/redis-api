from django.db.models import Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.churches.choices import MinistryKind
from apps.churches.models import Ministry
from apps.churches.permissions import can_manage_campus, general_led_kinds, led_campuses

from .models import Reconciliation


def _local_reconciliacao_leader(person) -> bool:
    return Ministry.objects.filter(
        kind=MinistryKind.RECONCILIACAO, leaderships__person=person
    ).exists()


def can_triage_campus(person, campus) -> bool:
    """Who may triage/assign leads already pinned to this campus: manager, that
    campus's leader/co-leader, the general leader of Reconciliação, or the
    leader/co-leader of the local Reconciliação Ministry at that campus."""
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    if can_manage_campus(person, campus):
        return True
    if MinistryKind.RECONCILIACAO in general_led_kinds(person):
        return True
    ministry = Ministry.objects.filter(campus=campus, kind=MinistryKind.RECONCILIACAO).first()
    return bool(ministry and ministry.leaderships.filter(person=person).exists())


def can_triage_lead(person, lead) -> bool:
    """Unassigned leads (campus not yet set) are visible/triageable by ANY campus
    leader or local/general Reconciliação leader — they self-select which ones are
    theirs from raw_campus_label. Once a lead has a campus, only that campus's
    triage authority applies."""
    if not person.is_authenticated:
        return False
    if person.is_manager or MinistryKind.RECONCILIACAO in general_led_kinds(person):
        return True
    if lead.campus_id:
        return can_triage_campus(person, lead.campus)
    return led_campuses(person).exists() or _local_reconciliacao_leader(person)


def can_manage_reconciliation(person, reconciliation) -> bool:
    """Manager, triage-level leader for that campus, OR the assigned reconciliador
    themselves (for jornada/day-to-day updates — NOT for reassignment, see the
    `reassign` view action which deliberately uses can_triage_campus instead)."""
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    if reconciliation.reconciliador_id == person.pk:
        return True
    return can_triage_campus(person, reconciliation.campus)


def scope_reconciliation_leads(qs, person):
    if person.is_manager or MinistryKind.RECONCILIACAO in general_led_kinds(person):
        return qs
    if not (led_campuses(person).exists() or _local_reconciliacao_leader(person)):
        return qs.none()
    condition = (
        Q(campus__isnull=True)
        | Q(campus__leaderships__person=person)
        | Q(campus__ministries__kind=MinistryKind.RECONCILIACAO, campus__ministries__leaderships__person=person)
    )
    return qs.filter(condition).distinct()


def scope_reconciliations(qs, person):
    if person.is_manager or MinistryKind.RECONCILIACAO in general_led_kinds(person):
        return qs
    condition = (
        Q(reconciliador=person)
        | Q(campus__leaderships__person=person)
        | Q(campus__ministries__kind=MinistryKind.RECONCILIACAO, campus__ministries__leaderships__person=person)
    )
    return qs.filter(condition).distinct()


def is_reconciliador_of(person, convert) -> bool:
    """Used by apps.people.permissions.PersonPermission — the reconciliador of a
    convert may view/edit that convert's Person record (chiefly: baptized/baptized_at)."""
    if not person.is_authenticated:
        return False
    return Reconciliation.objects.filter(convert=convert, reconciliador=person).exists()


class ReconciliationLeadPermission(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_triage_lead(request.user, obj)


class ReconciliationPermission(BasePermission):
    """Read: anyone who can see the row (queryset already scoped).
    Update jornada / add-to-cell / add-to-ministry: manager, triage-level leader,
    or the assigned reconciliador. Reassignment is gated separately in the view
    (triage-level only — see `reassign` action) since the reconciliador must NOT
    be able to reassign their own converts away."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_reconciliation(request.user, obj)

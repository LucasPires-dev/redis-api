import pytest
from django.utils import timezone

from apps.churches.choices import MinistryKind
from apps.churches.models import MinistryGeneralLeadership
from apps.production.choices import DemandType
from apps.production.models import Demand, Event
from apps.production.permissions import (
    can_create_event,
    can_manage_demand_source,
    can_manage_demand_target,
    can_manage_event,
    can_report_item_status,
    scope_demands,
    scope_events,
)

pytestmark = pytest.mark.django_db


def _event(**kwargs):
    kwargs.setdefault("title", "Culto de Domingo")
    kwargs.setdefault("starts_at", timezone.now())
    return Event.objects.create(**kwargs)


def test_manager_can_manage_and_create_any_event(manager, campus, jovens_ministry):
    event = _event(campus=campus, ministry_kind=MinistryKind.JOVENS)
    assert can_manage_event(manager, event)
    assert can_create_event(manager, MinistryKind.JOVENS, campus)


def test_local_leader_can_manage_own_ministry_event_only(jovens_leader, campus, other_campus, jovens_ministry):
    local_event = _event(campus=campus, ministry_kind=MinistryKind.JOVENS)
    assert can_manage_event(jovens_leader, local_event)
    assert can_create_event(jovens_leader, MinistryKind.JOVENS, campus)

    other_campus_event = _event(campus=other_campus, ministry_kind=MinistryKind.JOVENS)
    assert not can_manage_event(jovens_leader, other_campus_event)
    assert not can_create_event(jovens_leader, MinistryKind.JOVENS, other_campus)


def test_general_leader_can_manage_event_at_any_campus(regular_person, campus, other_campus):
    leadership = MinistryGeneralLeadership.objects.create(kind=MinistryKind.JOVENS)
    leadership.leaders.add(regular_person)
    event_here = _event(campus=campus, ministry_kind=MinistryKind.JOVENS)
    event_there = _event(campus=other_campus, ministry_kind=MinistryKind.JOVENS)
    assert can_manage_event(regular_person, event_here)
    assert can_manage_event(regular_person, event_there)
    assert can_create_event(regular_person, MinistryKind.JOVENS, None)


def test_regular_person_cannot_manage_or_create_event(regular_person, campus):
    event = _event(campus=campus, ministry_kind=MinistryKind.JOVENS)
    assert not can_manage_event(regular_person, event)
    assert not can_create_event(regular_person, MinistryKind.JOVENS, campus)


def test_campus_leader_can_manage_local_events_regardless_of_ministry(campus_leader, campus, jovens_ministry):
    event = _event(campus=campus, ministry_kind=MinistryKind.JOVENS)
    assert can_manage_event(campus_leader, event)


def test_can_report_item_status_allows_ministry_participant_not_event_owner(
    media_member, campus, jovens_ministry, media_ministry
):
    from apps.production.models import RundownItem

    event = _event(campus=campus, ministry_kind=MinistryKind.JOVENS)
    item = RundownItem.objects.create(event=event, order=1, title="Abertura", ministry_kind=MinistryKind.PALCO)
    # media_member does not manage the event (owned by Jovens), but reports status
    # for their own ministry (Mídia) on this item.
    assert can_report_item_status(media_member, item, MinistryKind.MEDIA)


def test_can_report_item_status_denies_unrelated_person(regular_person, campus, jovens_ministry):
    from apps.production.models import RundownItem

    event = _event(campus=campus, ministry_kind=MinistryKind.JOVENS)
    item = RundownItem.objects.create(event=event, order=1, title="Abertura", ministry_kind=MinistryKind.PALCO)
    assert not can_report_item_status(regular_person, item, MinistryKind.MEDIA)


def _demand(**kwargs):
    kwargs.setdefault("demand_type", DemandType.SLIDE)
    return Demand.objects.create(**kwargs)


def test_demand_source_and_target_permissions(jovens_leader, media_leader, regular_person, campus):
    demand = _demand(
        from_ministry_kind=MinistryKind.JOVENS,
        from_campus=campus,
        to_ministry_kind=MinistryKind.MEDIA,
        to_campus=campus,
    )
    assert can_manage_demand_source(jovens_leader, demand)
    assert not can_manage_demand_source(media_leader, demand)
    assert can_manage_demand_target(media_leader, demand)
    assert not can_manage_demand_target(jovens_leader, demand)
    assert not can_manage_demand_source(regular_person, demand)
    assert not can_manage_demand_target(regular_person, demand)


def test_scope_events_local_leader_sees_only_own_campus_ministry_events(
    jovens_leader, campus, other_campus, jovens_ministry
):
    mine = _event(campus=campus, ministry_kind=MinistryKind.JOVENS)
    _event(campus=other_campus, ministry_kind=MinistryKind.JOVENS)
    result = set(scope_events(Event.objects.all(), jovens_leader))
    assert result == {mine}


def test_scope_events_regular_culto_visible_to_everyone(regular_person):
    culto = _event(campus=None, ministry_kind=None)
    result = set(scope_events(Event.objects.all(), regular_person))
    assert result == {culto}


def test_scope_demands_received_only_shows_target_side(jovens_leader, media_leader, campus):
    demand = _demand(
        from_ministry_kind=MinistryKind.JOVENS,
        from_campus=campus,
        to_ministry_kind=MinistryKind.MEDIA,
        to_campus=campus,
    )
    assert set(scope_demands(Demand.objects.all(), media_leader, direction="received")) == {demand}
    assert set(scope_demands(Demand.objects.all(), jovens_leader, direction="received")) == set()
    assert set(scope_demands(Demand.objects.all(), jovens_leader, direction="sent")) == {demand}

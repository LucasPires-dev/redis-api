"""Visibility fixes found while documenting the events screens:

- a church-wide producer sees (and manages) every event — the old `|= Q()` did nothing;
- a church-wide event belongs to church-wide producers, not to campus producers;
- an event open for registration is visible to everyone at its campus;
- registering requires seeing the event (knowing its id is not enough);
- demands tell each side what it may do (can_answer / can_edit).
"""

import pytest
from django.utils import timezone

from apps.churches.choices import MinistryKind
from apps.payments.models import EventTicketConfig
from apps.production.choices import DemandType, EventType
from apps.production.models import Demand, Event, EventProducerAssignment
from apps.production.permissions import can_create_event, can_manage_event, scope_events
from tests.conftest import make_person

pytestmark = pytest.mark.django_db


def _event(**kwargs):
    kwargs.setdefault("title", "Conferência")
    kwargs.setdefault("event_type", EventType.CONFERENCE)
    kwargs.setdefault("starts_at", timezone.now() + timezone.timedelta(days=10))
    return Event.objects.create(**kwargs)


@pytest.fixture
def general_producer(campus):
    person = make_person(email="geral.eventos@ilan.church", name="Produtor Geral", campus=campus)
    EventProducerAssignment.objects.create(person=person, campus=None)
    return person


@pytest.fixture
def campus_producer(campus):
    person = make_person(email="eventos.arena@ilan.church", name="Produtor Arena", campus=campus)
    EventProducerAssignment.objects.create(person=person, campus=campus)
    return person


def test_general_producer_sees_and_manages_every_campus_event(general_producer, campus, other_campus):
    here, there = _event(campus=campus), _event(campus=other_campus)
    visible = set(scope_events(Event.objects.all(), general_producer))
    assert {here, there} <= visible
    assert can_manage_event(general_producer, there)


def test_campus_producer_sees_own_campus_only(campus_producer, campus, other_campus):
    here, there = _event(campus=campus), _event(campus=other_campus)
    visible = set(scope_events(Event.objects.all(), campus_producer))
    assert here in visible and there not in visible


def test_church_wide_event_belongs_to_general_producer(general_producer, campus_producer):
    church_wide = _event(campus=None)
    assert can_manage_event(general_producer, church_wide)
    assert can_create_event(general_producer, EventType.CONFERENCE, None, None)
    assert not can_manage_event(campus_producer, church_wide)
    assert not can_create_event(campus_producer, EventType.CONFERENCE, None, None)


def test_event_with_registration_is_visible_to_its_campus(regular_person, campus, other_campus):
    open_here = _event(campus=campus)
    EventTicketConfig.objects.create(event=open_here, price_cents=5000)
    closed_here = _event(campus=campus, title="Reunião interna")
    open_there = _event(campus=other_campus)
    EventTicketConfig.objects.create(event=open_there, price_cents=0)
    visible = set(scope_events(Event.objects.all(), regular_person))
    assert open_here in visible
    assert closed_here not in visible
    assert open_there not in visible


def test_cannot_register_for_an_event_one_cannot_see(api_client, regular_person, other_campus):
    hidden = _event(campus=other_campus)
    EventTicketConfig.objects.create(event=hidden, price_cents=0)
    api_client.force_authenticate(user=regular_person)
    response = api_client.post("/api/payments/registrations/", {"event_id": str(hidden.id)}, format="json")
    assert response.status_code == 400
    assert "event_id" in response.json()


def test_can_register_for_free_event_at_own_campus(api_client, regular_person, campus):
    event = _event(campus=campus)
    EventTicketConfig.objects.create(event=event, price_cents=0)
    api_client.force_authenticate(user=regular_person)
    response = api_client.post("/api/payments/registrations/", {"event_id": str(event.id)}, format="json")
    assert response.status_code == 201, response.content


def test_demand_flags_per_side(api_client, campus_producer, media_leader, campus):
    event = _event(campus=campus)
    demand = Demand.objects.create(
        event=event, from_ministry_kind=None, to_ministry_kind=MinistryKind.MEDIA, to_campus=campus,
        demand_type=DemandType.SLIDE, description="Slides", created_by=campus_producer,
    )
    api_client.force_authenticate(user=campus_producer)
    data = api_client.get(f"/api/production-demands/{demand.id}/").json()
    assert data["can_edit"] is True and data["can_answer"] is False

    api_client.force_authenticate(user=media_leader)
    data = api_client.get(f"/api/production-demands/{demand.id}/").json()
    assert data["can_answer"] is True and data["can_edit"] is False


def test_event_can_manage_flag(api_client, campus_leader, regular_person, campus):
    event = _event(campus=campus, event_type=EventType.CULTO)
    api_client.force_authenticate(user=campus_leader)
    assert api_client.get(f"/api/production-events/{event.id}/").json()["can_manage"] is True
    EventTicketConfig.objects.create(event=event, price_cents=0)
    api_client.force_authenticate(user=regular_person)
    assert api_client.get(f"/api/production-events/{event.id}/").json()["can_manage"] is False

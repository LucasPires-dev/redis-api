"""Event producers: who appoints whom (same general + local pattern as courses).

- manager → chooses church-wide (general) producers, who produce on every campus;
- general producer or campus leader → appoints the producers of a campus;
- campus producer → produces at their campus, but doesn't appoint peers.
"""

import pytest

from apps.production.choices import EventType
from apps.production.models import EventProducerAssignment
from apps.production.permissions import can_create_event, is_event_producer
from tests.conftest import make_person

pytestmark = pytest.mark.django_db


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def campus_url(campus):
    return f"/api/campuses/{campus.id}/event-producers/"


def general_url(church):
    return f"/api/churches/{church.id}/event-general-producers/"


@pytest.fixture
def general_producer(campus):
    person = make_person(email="produtor.geral@ilan.church", name="Produtor Geral", campus=campus)
    EventProducerAssignment.objects.create(person=person, campus=None)
    return person


@pytest.fixture
def campus_producer(campus):
    person = make_person(email="produtor.campus@ilan.church", name="Produtor Campus", campus=campus)
    EventProducerAssignment.objects.create(person=person, campus=campus)
    return person


# --- General producers (manager only) ---------------------------------------------------


def test_manager_chooses_general_producer(api_client, manager, regular_person, church, other_campus):
    response = auth(api_client, manager).post(
        general_url(church), {"person_id": str(regular_person.id)}, format="json"
    )

    assert response.status_code == 201, response.data
    # Church-wide: produces on any campus, not only their own.
    assert is_event_producer(regular_person, other_campus)


def test_choosing_the_same_general_producer_twice_does_not_duplicate(api_client, manager, regular_person, church):
    # NULL campus values don't collide in the DB unique constraint, so the view guards it.
    client = auth(api_client, manager)
    client.post(general_url(church), {"person_id": str(regular_person.id)}, format="json")
    client.post(general_url(church), {"person_id": str(regular_person.id)}, format="json")

    assert EventProducerAssignment.objects.filter(person=regular_person, campus__isnull=True).count() == 1


def test_manager_removes_general_producer(api_client, manager, general_producer, church):
    response = auth(api_client, manager).delete(f"{general_url(church)}{general_producer.id}/")

    assert response.status_code == 204
    assert not EventProducerAssignment.objects.filter(person=general_producer).exists()


@pytest.mark.parametrize("chooser", ["campus_leader", "general_producer", "regular_person"])
def test_only_manager_chooses_general_producers(api_client, request, church, regular_person, chooser):
    person = request.getfixturevalue(chooser)
    target = make_person(email="alvo@ilan.church", name="Alvo")

    response = auth(api_client, person).post(general_url(church), {"person_id": str(target.id)}, format="json")

    assert response.status_code == 403


# --- Campus producers --------------------------------------------------------------------


@pytest.mark.parametrize("appointer", ["manager", "campus_leader", "general_producer"])
def test_levels_above_appoint_campus_producer(api_client, request, regular_person, campus, appointer):
    person = request.getfixturevalue(appointer)

    response = auth(api_client, person).post(
        campus_url(campus), {"person_id": str(regular_person.id)}, format="json"
    )

    assert response.status_code == 201, response.data
    assert [p["id"] for p in response.data] == [str(regular_person.id)]
    assert is_event_producer(regular_person, campus)


def test_campus_producer_cannot_appoint_peers(api_client, campus_producer, regular_person, campus):
    response = auth(api_client, campus_producer).post(
        campus_url(campus), {"person_id": str(regular_person.id)}, format="json"
    )

    assert response.status_code == 403


def test_campus_leader_cannot_appoint_at_another_campus(api_client, campus_leader, other_campus):
    person_there = make_person(email="la@ilan.church", name="De Lá", campus=other_campus)

    response = auth(api_client, campus_leader).post(
        campus_url(other_campus), {"person_id": str(person_there.id)}, format="json"
    )

    assert response.status_code == 403


def test_campus_producer_must_belong_to_the_campus(api_client, campus_leader, campus, other_campus):
    outsider = make_person(email="fora@ilan.church", name="De Fora", campus=other_campus)

    response = auth(api_client, campus_leader).post(
        campus_url(campus), {"person_id": str(outsider.id)}, format="json"
    )

    assert response.status_code == 400
    assert "person_id" in response.data


def test_campus_leader_removes_campus_producer(api_client, campus_leader, campus_producer, campus):
    response = auth(api_client, campus_leader).delete(f"{campus_url(campus)}{campus_producer.id}/")

    assert response.status_code == 204
    assert not is_event_producer(campus_producer, campus)


# --- Effect of the appointment ----------------------------------------------------------


def test_campus_producer_creates_producer_events_only_at_their_campus(campus_producer, campus, other_campus):
    # Conferences/special services/trainings are created by producers.
    assert can_create_event(campus_producer, EventType.CONFERENCE, None, campus)
    assert not can_create_event(campus_producer, EventType.CONFERENCE, None, other_campus)


def test_general_producer_creates_producer_events_on_any_campus(general_producer, campus, other_campus):
    assert can_create_event(general_producer, EventType.CONFERENCE, None, campus)
    assert can_create_event(general_producer, EventType.CONFERENCE, None, other_campus)

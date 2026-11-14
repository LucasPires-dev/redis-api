"""Who may find a cell, a course class or an event: the church's members, or
everyone. People without a church link only reach what is public; nothing
"members only" of another church shows up."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.models import Campus, Church
from apps.courses.models import CourseClass
from apps.payments.models import EventTicketConfig
from apps.production.models import Event
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def as_(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


@pytest.fixture
def other_church_campus(db):
    church = Church.objects.create(name="Outra Igreja", abbreviation="OI")
    return Campus.objects.create(church=church, name="Sede OI")


@pytest.fixture
def outsider(db):
    """Signed up, not linked to any church yet."""
    return make_person(email="visitante@gmail.com", name="Visitante", campus=None)


def open_ids(client):
    return {c["id"] for c in client.get("/api/course-classes/open/").data}


# ── Course classes ──────────────────────────────────────────────────────────


def test_members_only_class_is_for_the_church(api_client, free_class, regular_person, outsider, other_church_campus):
    stranger = make_person(email="de.fora@oi.church", campus=other_church_campus)
    assert str(free_class.id) in open_ids(as_(api_client, regular_person))
    assert str(free_class.id) not in open_ids(as_(api_client, outsider))
    assert str(free_class.id) not in open_ids(as_(api_client, stranger))

    res = as_(api_client, outsider).post(f"/api/course-classes/{free_class.id}/enroll/", {"items": []}, format="json")
    assert res.status_code == 403 and "só para membros" in str(res.data)


def test_public_class_reaches_everyone(api_client, free_class, outsider, other_church_campus):
    free_class.visibility = "public"
    free_class.save()
    stranger = make_person(email="de.fora@oi.church", campus=other_church_campus)
    for person in (outsider, stranger):
        rows = as_(api_client, person).get("/api/course-classes/open/").data
        row = next(r for r in rows if r["id"] == str(free_class.id))
        assert row["church_name"] == "Igreja Luz às Nações" and row["visibility"] == "public"
    res = as_(api_client, outsider).post(f"/api/course-classes/{free_class.id}/enroll/", {"items": []}, format="json")
    assert res.status_code == 201, res.data


def test_other_churches_members_only_classes_stay_hidden(api_client, regular_person, other_church_campus):
    from apps.courses.models import Course

    course = Course.objects.create(church=other_church_campus.church, name="Curso OI")
    hidden = CourseClass.objects.create(
        course=course, campus=other_church_campus, name="Só OI", self_enrollment=True,
        end_date=TODAY + timedelta(days=30),
    )
    assert str(hidden.id) not in open_ids(as_(api_client, regular_person))


def test_coordinators_set_who_may_see_a_class(api_client, courses_leader, free_class):
    res = as_(api_client, courses_leader).patch(
        f"/api/course-classes/{free_class.id}/", {"visibility": "public"}, format="json"
    )
    assert res.status_code == 200, res.data
    free_class.refresh_from_db()
    assert free_class.visibility == "public"


# ── Events ──────────────────────────────────────────────────────────────────


def visible(client):
    return {e["id"] for e in client.get("/api/production-events/?page_size=200").data["results"]}


@pytest.fixture
def general_service(campus, manager):
    return Event.objects.create(
        title="Culto geral", starts_at=timezone.now() + timedelta(days=3), created_by=manager
    )


def test_an_event_knows_its_church(campus, general_service, other_church_campus):
    local = Event.objects.create(title="Local", campus=other_church_campus, starts_at=timezone.now())
    assert local.church_id == other_church_campus.church_id
    assert general_service.church_id == campus.church_id  # from its creator


def test_the_general_service_is_for_its_church(
    api_client, general_service, regular_person, outsider, other_church_campus
):
    stranger = make_person(email="de.fora@oi.church", campus=other_church_campus)
    assert str(general_service.id) in visible(as_(api_client, regular_person))
    assert str(general_service.id) not in visible(as_(api_client, outsider))
    assert str(general_service.id) not in visible(as_(api_client, stranger))


def test_public_events_reach_everyone(api_client, general_service, outsider):
    general_service.visibility = "public"
    general_service.save()
    assert str(general_service.id) in visible(as_(api_client, outsider))

    # The public page works with or without registration.
    api_client.force_authenticate(user=None)
    res = api_client.get(f"/api/payments/public-events/{general_service.id}/")
    assert res.status_code == 200 and res.data["ticket"] is None and res.data["registration_open"] is False
    EventTicketConfig.objects.create(event=general_service, price_cents=0)
    res = api_client.get(f"/api/payments/public-events/{general_service.id}/")
    assert res.status_code == 200 and res.data["church"] == "Igreja Luz às Nações"
    assert res.data["registration_open"] is True


def test_a_general_event_with_tickets_is_for_the_whole_church(api_client, manager, other_campus):
    event = Event.objects.create(
        title="Conferência", ministry_kind="jovens", starts_at=timezone.now() + timedelta(days=9), created_by=manager
    )
    EventTicketConfig.objects.create(event=event, price_cents=0)
    member_elsewhere = make_person(email="outro.campus@ilan.church", campus=other_campus)
    assert str(event.id) in visible(as_(api_client, member_elsewhere))


def test_copies_keep_who_may_see_them(api_client, manager, general_service):
    general_service.visibility = "public"
    general_service.save()
    res = as_(api_client, manager).post(
        f"/api/production-events/{general_service.id}/duplicate/",
        {"starts_at": (general_service.starts_at + timedelta(days=7)).isoformat()},
        format="json",
    )
    assert res.status_code == 201, res.data
    assert res.data["visibility"] == "public" and res.data["church"]["name"] == "Igreja Luz às Nações"


# ── Cells ───────────────────────────────────────────────────────────────────


def test_cells_have_who_may_see_them(api_client, manager, campus):
    cell = make_cell(campus=campus, name="GT Aberto")
    assert cell.visibility == "members"
    res = as_(api_client, manager).patch(f"/api/cells/{cell.id}/", {"visibility": "public"}, format="json")
    assert res.status_code == 200, res.data
    assert res.data["visibility"] == "public"
    assert as_(api_client, manager).patch(
        f"/api/cells/{cell.id}/", {"visibility": "everyone"}, format="json"
    ).status_code == 400

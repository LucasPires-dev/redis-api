"""Notification center: the API, `notify()` and the features that raise notifications."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import CellRole, JoinRequestStatus, LeadershipRole
from apps.churches.models import CampusJoinRequest, CampusLeadership, CellLeadership
from apps.courses import billing
from apps.courses.choices import EnrollmentPaymentStatus, PaymentMethod, TeacherRole
from apps.courses.models import (
    Course,
    CourseClass,
    CourseClassTeacher,
    CourseGeneralLeadership,
    Enrollment,
)
from apps.notifications.models import Notification
from apps.notifications.service import notify
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


@pytest.fixture
def member(campus):
    return make_person(email="membro.n@ilan.church", name="Membro", campus=campus)


@pytest.fixture
def other(campus):
    return make_person(email="outro.n@ilan.church", name="Outro", campus=campus)


def in_order(*created):
    """Spaces out notifications created in a row (the clock may not tick between them)."""
    for minutes, (note,) in enumerate(created):
        Notification.objects.filter(pk=note.pk).update(created_at=timezone.now() + timedelta(minutes=minutes))
    return [note for (note,) in created]


def titles(person):
    return list(Notification.objects.filter(recipient=person).values_list("title", flat=True))


# ── API ──────────────────────────────────────────────────────────────────────


def test_lists_only_own_notifications_newest_first(api_client, member, other):
    in_order(notify(member, "test", "Primeiro"), notify(member, "test", "Segundo"))
    notify(other, "test", "De outra pessoa")

    res = auth(api_client, member).get("/api/notifications/")
    assert res.status_code == 200
    assert [n["title"] for n in res.data["results"]] == ["Segundo", "Primeiro"]
    assert set(res.data["results"][0]) == {"id", "kind", "title", "body", "link", "read_at", "created_at"}


def test_unread_count_read_and_read_all(api_client, member, other):
    first, _, _ = in_order(notify(member, "test", "Um"), notify(member, "test", "Dois"), notify(member, "test", "Três"))
    theirs, = notify(other, "test", "Alheia")
    client = auth(api_client, member)

    assert client.get("/api/notifications/unread-count/").data == {"count": 3}

    res = client.post(f"/api/notifications/{first.id}/read/")
    assert res.status_code == 200 and res.data["read_at"]
    assert client.get("/api/notifications/unread-count/").data == {"count": 2}
    unread = client.get("/api/notifications/?unread=1").data["results"]
    assert [n["title"] for n in unread] == ["Três", "Dois"]

    # Someone else's notification can't be touched.
    assert client.post(f"/api/notifications/{theirs.id}/read/").status_code == 404

    assert client.post("/api/notifications/read-all/").data == {"updated": 2}
    assert client.get("/api/notifications/unread-count/").data == {"count": 0}
    assert Notification.objects.get(pk=theirs.id).read_at is None


def test_requires_login(api_client):
    assert api_client.get("/api/notifications/").status_code == 401


# ── notify() ─────────────────────────────────────────────────────────────────


def test_dedupe_key_notifies_once(member, other):
    notify(member, "test", "Vaga", dedupe_key="offer:1")
    notify([member, other], "test", "Vaga", dedupe_key="offer:1")
    assert titles(member) == ["Vaga"]
    assert titles(other) == ["Vaga"]


def test_notify_never_breaks_the_caller(member, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(Notification.objects, "bulk_create", boom)
    assert notify(member, "test", "Nada") == []


def test_pushes_to_open_screens_after_commit(member, django_capture_on_commit_callbacks, monkeypatch):
    pushed = []
    monkeypatch.setattr("apps.notifications.service._push", lambda ids, title: pushed.append((ids, title)))
    with django_capture_on_commit_callbacks(execute=True):
        notify(member, "test", "Olá")
    assert pushed == [([member.pk], "Olá")]


# ── Courses ──────────────────────────────────────────────────────────────────


@pytest.fixture
def course(church):
    return Course.objects.create(church=church, name="Fundamentos")


@pytest.fixture
def full_class(course, campus):
    return CourseClass.objects.create(
        course=course, campus=campus, name="Turma A", self_enrollment=True, max_capacity=1,
        reservation_days=2, end_date=TODAY + timedelta(days=30),
    )


def test_waitlist_offer_notifies_the_next_in_line(api_client, full_class, member, other):
    enrolled = auth(api_client, other).post(f"/api/course-classes/{full_class.id}/enroll/", {}, format="json")
    assert enrolled.status_code == 201
    assert auth(api_client, member).post(f"/api/course-classes/{full_class.id}/waitlist/").status_code == 201
    assert titles(member) == []

    auth(api_client, other).post(f"/api/course-enrollments/{enrolled.data['id']}/cancel/", {}, format="json")
    # Seats are recounted lazily (e.g. when the open classes are listed).
    auth(api_client, member).get("/api/course-classes/open/")
    auth(api_client, member).get("/api/course-classes/open/")

    notes = Notification.objects.filter(recipient=member)
    assert notes.count() == 1
    note = notes.get()
    assert note.kind == "waitlist_offer"
    assert note.title == "Abriu uma vaga: Fundamentos — Turma A"
    assert "reservada para você até" in note.body
    assert note.link == "/app/cursos"


def test_payment_received_notifies_the_student(full_class, member):
    enrollment = Enrollment.objects.create(
        course_class=full_class, person=member, amount_cents=15000,
        payment_status=EnrollmentPaymentStatus.PENDING,
    )
    billing.record_payment(enrollment, 5000, PaymentMethod.MANUAL)
    billing.record_payment(enrollment, 10000, PaymentMethod.MANUAL)

    bodies = set(Notification.objects.filter(recipient=member).values_list("body", flat=True))
    assert bodies == {
        "Recebemos R$ 50,00. Falta R$ 100,00.",
        "Recebemos R$ 100,00. Sua inscrição está quitada.",
    }


def test_completing_the_class_notifies_the_certificate(api_client, full_class, member, courses_teacher):
    Enrollment.objects.create(course_class=full_class, person=member)
    res = auth(api_client, courses_teacher).post(
        f"/api/course-classes/{full_class.id}/complete/", {"person_ids": [str(member.id)]}, format="json"
    )
    assert res.status_code == 200
    note = Notification.objects.get(recipient=member, kind="course_completed")
    assert note.title == "Você concluiu Fundamentos!"
    assert note.link == "/app/cursos/matriculas"


@pytest.fixture
def courses_teacher(full_class, campus):
    teacher = make_person(email="prof.n@ilan.church", name="Professora", campus=campus)
    CourseClassTeacher.objects.create(course_class=full_class, person=teacher, role=TeacherRole.TEACHER)
    return teacher


# ── Joining a campus ─────────────────────────────────────────────────────────


def test_join_request_notifies_leadership_then_the_person(api_client, campus):
    leader = make_person(email="lider.campus.n@ilan.church", name="Líder", campus=campus)
    CampusLeadership.objects.create(campus=campus, person=leader, role=LeadershipRole.LEADER)
    newcomer = make_person(email="novo.n@ilan.church", name="Pessoa Nova")

    res = auth(api_client, newcomer).post("/api/campus-join-requests/", {"campus_id": str(campus.id)}, format="json")
    assert res.status_code == 201
    note = Notification.objects.get(recipient=leader, kind="join_request")
    assert note.title == "Novo pedido de vínculo: Pessoa Nova"
    assert note.link == f"/app/campus/{campus.id}#pedidos"

    request_id = CampusJoinRequest.objects.get(person=newcomer, status=JoinRequestStatus.PENDING).id
    assert auth(api_client, leader).post(f"/api/campus-join-requests/{request_id}/approve/").status_code == 200
    assert titles(newcomer) == [f"Bem-vindo(a) ao campus {campus.name}!"]


def test_join_request_without_campus_leadership_goes_to_managers(api_client, campus):
    manager = make_person(email="gestor.n@ilan.church", name="Gestor", is_manager=True)
    newcomer = make_person(email="novo2.n@ilan.church", name="Outra Pessoa")
    auth(api_client, newcomer).post("/api/campus-join-requests/", {"campus_id": str(campus.id)}, format="json")
    assert titles(manager) == ["Novo pedido de vínculo: Outra Pessoa"]


# ── Appointments ─────────────────────────────────────────────────────────────


def test_appointments_notify_the_person(campus, church, member, other, full_class):
    cell = make_cell(campus=campus, name="Célula Esperança")
    CellLeadership.objects.create(cell=cell, person=member, role=CellRole.TRAINEE)
    CourseClassTeacher.objects.create(course_class=full_class, person=member, role=TeacherRole.ASSISTANT)
    general, _ = CourseGeneralLeadership.objects.get_or_create(church=church)
    general.leaders.add(other)
    other.campus_leaderships.create(campus=campus, role=LeadershipRole.CO_LEADER)

    assert set(titles(member)) == {
        "Você agora é líder em treinamento da célula Célula Esperança",
        "Você agora é auxiliar da turma Turma A (Fundamentos)",
    }
    assert set(titles(other)) == {
        "Você agora é responsável geral pelos cursos",
        f"Você agora é colíder do campus {campus.name}",
    }
    cell_note = Notification.objects.get(recipient=member, title__contains="célula")
    assert cell_note.link == f"/app/celulas/{cell.id}"


def test_supervisor_appointment(api_client, campus, member):
    cell = make_cell(campus=campus, name="Célula Fé")
    manager = make_person(email="gestor2.n@ilan.church", name="Gestor", is_manager=True)
    url = f"/api/cells/{cell.id}/supervisor/"
    res = auth(api_client, manager).put(url, {"person_id": str(member.id)}, format="json")
    assert res.status_code == 200
    assert titles(member) == ["Você agora é supervisor(a) da célula Célula Fé"]
    # Saving the same supervisor again doesn't notify twice.
    auth(api_client, manager).put(url, {"person_id": str(member.id)}, format="json")
    assert len(titles(member)) == 1

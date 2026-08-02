import pytest
from django.utils import timezone

from apps.production.models import Demand, Event, EventChecklistItem, EventRosterEntry, RundownItem, RundownItemStatus

pytestmark = pytest.mark.django_db


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def test_ministry_leader_can_create_local_event(api_client, jovens_leader, campus, jovens_ministry):
    resp = auth(api_client, jovens_leader).post(
        "/api/production-events/",
        {
            "title": "Retiro de Jovens",
            "event_type": "ministry_event",
            "ministry_kind": "jovens",
            "campus_id": str(campus.id),
            "starts_at": timezone.now().isoformat(),
        },
    )
    assert resp.status_code == 201
    event = Event.objects.get()
    assert event.created_by_id == jovens_leader.id
    assert event.campus_id == campus.id


def test_regular_person_cannot_create_event(api_client, regular_person, campus):
    resp = auth(api_client, regular_person).post(
        "/api/production-events/",
        {
            "title": "Retiro de Jovens",
            "event_type": "ministry_event",
            "ministry_kind": "jovens",
            "campus_id": str(campus.id),
            "starts_at": timezone.now().isoformat(),
        },
    )
    assert resp.status_code == 403
    assert not Event.objects.exists()


def test_event_owner_can_add_rundown_item(api_client, jovens_leader, campus, jovens_ministry):
    event = Event.objects.create(
        title="Retiro de Jovens", campus=campus, ministry_kind="jovens", starts_at=timezone.now(),
        created_by=jovens_leader,
    )
    resp = auth(api_client, jovens_leader).post(
        "/api/production-rundown-items/",
        {"event_id": str(event.id), "order": 1, "title": "Abertura", "ministry_kind": "palco"},
    )
    assert resp.status_code == 201
    assert RundownItem.objects.filter(event=event, title="Abertura").exists()


def test_unrelated_ministry_cannot_add_rundown_item(api_client, media_leader, campus, jovens_ministry, jovens_leader):
    event = Event.objects.create(
        title="Retiro de Jovens", campus=campus, ministry_kind="jovens", starts_at=timezone.now(),
        created_by=jovens_leader,
    )
    resp = auth(api_client, media_leader).post(
        "/api/production-rundown-items/",
        {"event_id": str(event.id), "order": 1, "title": "Abertura", "ministry_kind": "palco"},
    )
    assert resp.status_code == 403


def test_media_member_can_set_item_status_for_their_own_ministry(
    api_client, media_member, campus, jovens_ministry, jovens_leader
):
    event = Event.objects.create(
        title="Retiro de Jovens", campus=campus, ministry_kind="jovens", starts_at=timezone.now(),
        created_by=jovens_leader,
    )
    item = RundownItem.objects.create(event=event, order=1, title="Abertura", ministry_kind="palco")

    resp = auth(api_client, media_member).post(
        f"/api/production-rundown-items/{item.id}/set-status/",
        {"ministry_kind": "media", "status": "attention"},
    )
    assert resp.status_code == 200
    status_row = RundownItemStatus.objects.get(rundown_item=item, ministry_kind="media")
    assert status_row.status == "attention"
    assert status_row.updated_by_id == media_member.id


def test_set_status_twice_updates_same_row(api_client, media_member, campus, jovens_ministry, jovens_leader):
    event = Event.objects.create(
        title="Culto", campus=campus, ministry_kind="jovens", starts_at=timezone.now(), created_by=jovens_leader
    )
    item = RundownItem.objects.create(event=event, order=1, title="Louvor 1", ministry_kind="palco")

    auth(api_client, media_member).post(
        f"/api/production-rundown-items/{item.id}/set-status/", {"ministry_kind": "media", "status": "problem"}
    )
    auth(api_client, media_member).post(
        f"/api/production-rundown-items/{item.id}/set-status/", {"ministry_kind": "media", "status": "ok"}
    )
    assert RundownItemStatus.objects.filter(rundown_item=item, ministry_kind="media").count() == 1
    assert RundownItemStatus.objects.get(rundown_item=item, ministry_kind="media").status == "ok"


def test_jovens_leader_can_create_demand_to_media(api_client, jovens_leader, campus, jovens_ministry, media_ministry):
    resp = auth(api_client, jovens_leader).post(
        "/api/production-demands/",
        {
            "from_ministry_kind": "jovens",
            "from_campus_id": str(campus.id),
            "to_ministry_kind": "media",
            "to_campus_id": str(campus.id),
            "demand_type": "slide",
            "priority": "urgent",
            "description": "Preparar slides do Retiro",
        },
    )
    assert resp.status_code == 201
    demand = Demand.objects.get()
    assert demand.status == "open"
    assert demand.created_by_id == jovens_leader.id


def test_demand_cannot_target_a_non_production_ministry(api_client, jovens_leader, campus, jovens_ministry):
    resp = auth(api_client, jovens_leader).post(
        "/api/production-demands/",
        {
            "from_ministry_kind": "jovens",
            "from_campus_id": str(campus.id),
            "to_ministry_kind": "homens",
            "to_campus_id": str(campus.id),
            "demand_type": "other",
        },
    )
    assert resp.status_code == 400


def test_media_leader_accepts_and_completes_demand(api_client, jovens_leader, media_leader, campus, jovens_ministry, media_ministry):
    demand = Demand.objects.create(
        from_ministry_kind="jovens", from_campus=campus, to_ministry_kind="media", to_campus=campus,
        demand_type="slide", created_by=jovens_leader,
    )
    resp = auth(api_client, media_leader).post(f"/api/production-demands/{demand.id}/accept/")
    assert resp.status_code == 200
    assert resp.data["status"] == "in_progress"

    resp = auth(api_client, media_leader).post(f"/api/production-demands/{demand.id}/complete/")
    assert resp.status_code == 200
    assert resp.data["status"] == "done"


def test_source_ministry_cannot_accept_their_own_demand(api_client, jovens_leader, campus, jovens_ministry, media_ministry):
    demand = Demand.objects.create(
        from_ministry_kind="jovens", from_campus=campus, to_ministry_kind="media", to_campus=campus,
        demand_type="slide", created_by=jovens_leader,
    )
    resp = auth(api_client, jovens_leader).post(f"/api/production-demands/{demand.id}/accept/")
    assert resp.status_code == 403


def test_media_leader_can_reject_demand_with_reason(api_client, jovens_leader, media_leader, campus, jovens_ministry, media_ministry):
    demand = Demand.objects.create(
        from_ministry_kind="jovens", from_campus=campus, to_ministry_kind="media", to_campus=campus,
        demand_type="slide", created_by=jovens_leader,
    )
    resp = auth(api_client, media_leader).post(
        f"/api/production-demands/{demand.id}/reject/", {"rejection_reason": "Sem equipe disponível"}
    )
    assert resp.status_code == 200
    demand.refresh_from_db()
    assert demand.status == "rejected"
    assert demand.rejection_reason == "Sem equipe disponível"


def test_demands_direction_filters_sent_and_received(api_client, jovens_leader, media_leader, campus, jovens_ministry, media_ministry):
    Demand.objects.create(
        from_ministry_kind="jovens", from_campus=campus, to_ministry_kind="media", to_campus=campus,
        demand_type="slide", created_by=jovens_leader,
    )
    resp = auth(api_client, jovens_leader).get("/api/production-demands/?direction=sent")
    assert resp.data["count"] == 1
    resp = auth(api_client, jovens_leader).get("/api/production-demands/?direction=received")
    assert resp.data["count"] == 0

    resp = auth(api_client, media_leader).get("/api/production-demands/?direction=received")
    assert resp.data["count"] == 1
    resp = auth(api_client, media_leader).get("/api/production-demands/?direction=sent")
    assert resp.data["count"] == 0


def test_culto_regular_visible_to_any_authenticated_person(api_client, regular_person):
    Event.objects.create(title="Culto de Domingo", starts_at=timezone.now())
    resp = auth(api_client, regular_person).get("/api/production-events/")
    assert resp.status_code == 200
    assert resp.data["count"] == 1


def test_event_rundown_nested_in_response(api_client, jovens_leader, campus, jovens_ministry):
    event = Event.objects.create(
        title="Culto", campus=campus, ministry_kind="jovens", starts_at=timezone.now(), created_by=jovens_leader
    )
    RundownItem.objects.create(event=event, order=1, title="Abertura", ministry_kind="palco")
    resp = auth(api_client, jovens_leader).get(f"/api/production-events/{event.id}/")
    assert resp.status_code == 200
    assert len(resp.data["rundown_items"]) == 1
    assert resp.data["rundown_items"][0]["title"] == "Abertura"


def _event_for_roster(campus, jovens_leader):
    return Event.objects.create(
        title="Retiro de Jovens", campus=campus, ministry_kind="jovens", starts_at=timezone.now(),
        created_by=jovens_leader,
    )


def test_ministry_leader_can_invite_member_to_roster(
    api_client, media_leader, media_ministry, media_member, campus, jovens_leader, jovens_ministry
):
    event = _event_for_roster(campus, jovens_leader)
    resp = auth(api_client, media_leader).post(
        "/api/production-roster-entries/",
        {
            "event_id": str(event.id),
            "ministry_id": str(media_ministry.id),
            "person_id": str(media_member.id),
            "function": "Câmera 2",
        },
    )
    assert resp.status_code == 201
    entry = EventRosterEntry.objects.get()
    assert entry.status == "invited"
    assert entry.invited_by_id == media_leader.id


def test_regular_person_cannot_invite_to_roster(
    api_client, regular_person, media_ministry, campus, jovens_leader, jovens_ministry, media_member
):
    event = _event_for_roster(campus, jovens_leader)
    resp = auth(api_client, regular_person).post(
        "/api/production-roster-entries/",
        {
            "event_id": str(event.id),
            "ministry_id": str(media_ministry.id),
            "person_id": str(media_member.id),
        },
    )
    assert resp.status_code == 403
    assert not EventRosterEntry.objects.exists()


def test_invitee_can_confirm_own_entry(
    api_client, media_leader, media_ministry, media_member, campus, jovens_leader, jovens_ministry
):
    event = _event_for_roster(campus, jovens_leader)
    entry = EventRosterEntry.objects.create(
        event=event, ministry=media_ministry, person=media_member, invited_by=media_leader
    )
    resp = auth(api_client, media_member).post(f"/api/production-roster-entries/{entry.id}/confirm/")
    assert resp.status_code == 200
    entry.refresh_from_db()
    assert entry.status == "confirmed"
    assert entry.responded_at is not None


def test_other_person_cannot_confirm_someone_elses_entry(
    api_client, regular_person, media_leader, media_ministry, media_member, campus, jovens_leader, jovens_ministry
):
    """regular_person has no relation to the event at all (not a member of
    jovens/media, not a campus leader) — scope_events hides it before the
    permission check even runs, so this 404s rather than 403s, same convention
    as apps.reconciliation's visibility tests."""
    event = _event_for_roster(campus, jovens_leader)
    entry = EventRosterEntry.objects.create(
        event=event, ministry=media_ministry, person=media_member, invited_by=media_leader
    )
    resp = auth(api_client, regular_person).post(f"/api/production-roster-entries/{entry.id}/confirm/")
    assert resp.status_code == 404
    entry.refresh_from_db()
    assert entry.status == "invited"


def test_ministry_leader_can_decline_on_behalf_of_member(
    api_client, media_leader, media_ministry, media_member, campus, jovens_leader, jovens_ministry
):
    event = _event_for_roster(campus, jovens_leader)
    entry = EventRosterEntry.objects.create(
        event=event, ministry=media_ministry, person=media_member, invited_by=media_leader
    )
    resp = auth(api_client, media_leader).post(f"/api/production-roster-entries/{entry.id}/decline/")
    assert resp.status_code == 200
    entry.refresh_from_db()
    assert entry.status == "declined"


def test_media_member_can_create_checklist_item_for_own_ministry(
    api_client, media_member, campus, jovens_leader, jovens_ministry
):
    event = _event_for_roster(campus, jovens_leader)
    resp = auth(api_client, media_member).post(
        "/api/production-checklist-items/",
        {"event_id": str(event.id), "ministry_kind": "media", "text": "Testar projetor"},
    )
    assert resp.status_code == 201
    assert EventChecklistItem.objects.filter(event=event, text="Testar projetor").exists()


def test_unrelated_person_cannot_create_checklist_item(
    api_client, regular_person, campus, jovens_leader, jovens_ministry
):
    event = _event_for_roster(campus, jovens_leader)
    resp = auth(api_client, regular_person).post(
        "/api/production-checklist-items/",
        {"event_id": str(event.id), "ministry_kind": "media", "text": "Testar projetor"},
    )
    assert resp.status_code == 403


def test_toggle_checklist_item_sets_and_clears_done_by(
    api_client, media_member, campus, jovens_leader, jovens_ministry
):
    event = _event_for_roster(campus, jovens_leader)
    item = EventChecklistItem.objects.create(event=event, ministry_kind="media", text="Testar projetor")

    resp = auth(api_client, media_member).post(f"/api/production-checklist-items/{item.id}/toggle/")
    assert resp.status_code == 200
    item.refresh_from_db()
    assert item.is_done is True
    assert item.done_by_id == media_member.id
    assert item.done_at is not None

    resp = auth(api_client, media_member).post(f"/api/production-checklist-items/{item.id}/toggle/")
    assert resp.status_code == 200
    item.refresh_from_db()
    assert item.is_done is False
    assert item.done_by_id is None
    assert item.done_at is None

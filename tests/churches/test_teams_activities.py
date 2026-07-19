import datetime

import pytest

from apps.churches.models import ActivityCompletion, TeamLeadership

pytestmark = pytest.mark.django_db


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


# --- Team creation & leadership -------------------------------------------------


def test_ministry_leader_can_create_team(api_client, leader, brave):
    resp = auth(api_client, leader).post(
        "/api/teams/", {"ministry_id": str(brave.id), "name": "Equipe Mídia"}
    )
    assert resp.status_code == 201


def test_regular_person_cannot_create_team(api_client, regular_person, brave):
    resp = auth(api_client, regular_person).post(
        "/api/teams/", {"ministry_id": str(brave.id), "name": "Equipe Mídia"}
    )
    assert resp.status_code == 403


def test_team_leader_cannot_assign_team_leadership(api_client, team_leader, team, regular_person):
    resp = auth(api_client, team_leader).post(
        f"/api/teams/{team.id}/leaderships/", {"person_id": str(regular_person.id), "role": "co_leader"}
    )
    assert resp.status_code == 403


def test_ministry_leader_can_assign_team_leadership(api_client, leader, team, regular_person):
    resp = auth(api_client, leader).post(
        f"/api/teams/{team.id}/leaderships/", {"person_id": str(regular_person.id), "role": "co_leader"}
    )
    assert resp.status_code == 201
    assert TeamLeadership.objects.filter(team=team, person=regular_person, role="co_leader").exists()


# --- Team membership must come from the ministry's members ----------------------


def test_cannot_add_non_ministry_member_to_team(api_client, team_leader, team, regular_person, brave):
    assert not brave.members.filter(pk=regular_person.pk).exists()
    resp = auth(api_client, team_leader).post(
        f"/api/teams/{team.id}/members/", {"person_id": str(regular_person.id)}
    )
    assert resp.status_code == 400


def test_can_add_ministry_member_to_team(api_client, team_leader, team, regular_person, brave):
    brave.members.add(regular_person)
    resp = auth(api_client, team_leader).post(
        f"/api/teams/{team.id}/members/", {"person_id": str(regular_person.id)}
    )
    assert resp.status_code == 201


# --- Activities -------------------------------------------------------------------


def test_team_leader_can_create_activity(api_client, team_leader, team):
    resp = auth(api_client, team_leader).post(
        "/api/activities/",
        {"team_id": str(team.id), "title": "Devocional", "kind": "daily", "points": 10},
    )
    assert resp.status_code == 201


def test_team_member_cannot_create_activity(api_client, team_member, team):
    resp = auth(api_client, team_member).post(
        "/api/activities/",
        {"team_id": str(team.id), "title": "Devocional", "kind": "daily", "points": 10},
    )
    assert resp.status_code == 403


def test_member_can_self_complete_daily_activity_and_again_next_day(
    api_client, team_member, daily_activity
):
    resp = auth(api_client, team_member).post(f"/api/activities/{daily_activity.id}/complete/", {})
    assert resp.status_code == 201
    assert resp.data["points_awarded"] == 10

    # Same day again -> rejected
    resp = auth(api_client, team_member).post(f"/api/activities/{daily_activity.id}/complete/", {})
    assert resp.status_code == 400

    # A different day -> allowed
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    resp = auth(api_client, team_member).post(
        f"/api/activities/{daily_activity.id}/complete/", {"date": tomorrow}
    )
    assert resp.status_code == 201
    assert ActivityCompletion.objects.filter(activity=daily_activity, person=team_member).count() == 2


def test_eventual_activity_can_only_be_completed_once(api_client, team_member, eventual_activity):
    resp = auth(api_client, team_member).post(f"/api/activities/{eventual_activity.id}/complete/", {})
    assert resp.status_code == 201

    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    resp = auth(api_client, team_member).post(
        f"/api/activities/{eventual_activity.id}/complete/", {"date": tomorrow}
    )
    assert resp.status_code == 400


def test_member_cannot_complete_on_behalf_of_someone_else(
    api_client, team_member, regular_person, daily_activity, team
):
    team.members.add(regular_person)
    resp = auth(api_client, team_member).post(
        f"/api/activities/{daily_activity.id}/complete/", {"person_id": str(regular_person.id)}
    )
    assert resp.status_code == 403


def test_team_leader_can_complete_on_behalf_of_a_member(api_client, team_leader, team_member, daily_activity):
    resp = auth(api_client, team_leader).post(
        f"/api/activities/{daily_activity.id}/complete/", {"person_id": str(team_member.id)}
    )
    assert resp.status_code == 201
    assert ActivityCompletion.objects.filter(activity=daily_activity, person=team_member).exists()


def test_personal_total_points_aggregates_completions(api_client, team_member, daily_activity):
    auth(api_client, team_member).post(f"/api/activities/{daily_activity.id}/complete/", {})
    resp = auth(api_client, team_member).get("/api/auth/me/")
    assert resp.data["total_points"] == 10


# --- Team points (collective, not tied to a person) ------------------------------


def test_team_leader_can_award_team_points(api_client, team_leader, team):
    resp = auth(api_client, team_leader).post(
        "/api/teams/{}/point-awards/".format(team.id), {"points": 30, "reason": "Etapa concluída"}
    )
    assert resp.status_code == 201

    resp = auth(api_client, team_leader).get(f"/api/teams/{team.id}/")
    assert resp.data["total_points"] == 30

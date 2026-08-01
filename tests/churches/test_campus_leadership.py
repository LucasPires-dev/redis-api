import pytest

from apps.churches.permissions import (
    can_manage_campus,
    can_manage_cell,
    can_manage_ministry,
    can_manage_team,
    scope_activities,
    scope_cells,
    scope_ministries,
    scope_teams,
)
from apps.churches.models import Activity, Cell, Team

pytestmark = pytest.mark.django_db


def test_manager_can_manage_any_campus(manager, campus, other_campus):
    assert can_manage_campus(manager, campus)
    assert can_manage_campus(manager, other_campus)


def test_campus_leader_and_co_leader_have_equivalent_access(campus_leader, campus_co_leader, campus, other_campus):
    assert can_manage_campus(campus_leader, campus)
    assert can_manage_campus(campus_co_leader, campus)
    assert not can_manage_campus(campus_leader, other_campus)
    assert not can_manage_campus(campus_co_leader, other_campus)


def test_regular_person_cannot_manage_campus(regular_person, campus):
    assert not can_manage_campus(regular_person, campus)


def test_campus_leader_can_manage_ministry_at_their_campus(campus_leader, brave, other_ministry):
    assert can_manage_ministry(campus_leader, brave)
    assert not can_manage_ministry(campus_leader, other_ministry)


def test_campus_leader_can_manage_cell_at_their_campus(campus_leader, campus, other_campus):
    cell = Cell.objects.create(campus=campus, name="GT Central")
    other_cell = Cell.objects.create(campus=other_campus, name="GT Fora")
    assert can_manage_cell(campus_leader, cell)
    assert not can_manage_cell(campus_leader, other_cell)


def test_scope_ministries_includes_campus_led_ministries(campus_leader, brave, other_ministry):
    result = set(scope_ministries(brave.__class__.objects.all(), campus_leader))
    assert result == {brave}


def test_scope_cells_includes_campus_led_cells(campus_leader, campus, other_campus):
    cell = Cell.objects.create(campus=campus, name="GT Central")
    Cell.objects.create(campus=other_campus, name="GT Fora")
    result = set(scope_cells(Cell.objects.all(), campus_leader))
    assert result == {cell}


def test_scope_teams_includes_campus_led_teams(campus_leader, brave, team, other_ministry):
    other_team = Team.objects.create(ministry=other_ministry, name="Outra Equipe")
    result = set(scope_teams(Team.objects.all(), campus_leader))
    assert team in result
    assert other_team not in result


def test_scope_activities_includes_campus_led_activities(campus_leader, daily_activity, team, other_ministry):
    other_team = Team.objects.create(ministry=other_ministry, name="Outra Equipe")
    other_activity = Activity.objects.create(team=other_team, title="Outra", kind="daily", points=5)
    result = set(scope_activities(Activity.objects.all(), campus_leader))
    assert daily_activity in result
    assert other_activity not in result


def test_can_manage_team_inherits_campus_leadership_via_ministry(campus_leader, team, other_ministry):
    other_team = Team.objects.create(ministry=other_ministry, name="Outra Equipe")
    assert can_manage_team(campus_leader, team)
    assert not can_manage_team(campus_leader, other_team)


def test_person_who_is_both_campus_and_ministry_leader_not_duplicated_in_scope(leader, campus, brave, other_ministry):
    from apps.churches.models import CampusLeadership
    from apps.churches.choices import LeadershipRole

    CampusLeadership.objects.create(campus=campus, person=leader, role=LeadershipRole.LEADER)
    result = list(scope_ministries(brave.__class__.objects.all(), leader))
    assert result.count(brave) == 1

import pytest

from apps.churches.choices import MinistryKind
from apps.churches.models import Ministry, MinistryGeneralLeadership
from apps.churches.permissions import can_manage_ministry, general_led_kinds, led_ministries, scope_ministries

pytestmark = pytest.mark.django_db


def test_manager_can_manage_any_ministry(manager, brave, other_ministry):
    assert can_manage_ministry(manager, brave)
    assert can_manage_ministry(manager, other_ministry)


def test_leader_and_co_leader_have_equivalent_access(leader, co_leader, brave, other_ministry):
    assert can_manage_ministry(leader, brave)
    assert can_manage_ministry(co_leader, brave)
    assert not can_manage_ministry(leader, other_ministry)
    assert not can_manage_ministry(co_leader, other_ministry)


def test_regular_person_cannot_manage_any_ministry(regular_person, brave, other_ministry):
    assert not can_manage_ministry(regular_person, brave)
    assert not can_manage_ministry(regular_person, other_ministry)


def test_led_ministries_includes_both_leader_and_co_leader(leader, co_leader, brave):
    assert list(led_ministries(leader)) == [brave]
    assert list(led_ministries(co_leader)) == [brave]


def test_general_leader_can_manage_ministry_of_their_kind_at_any_campus(
    regular_person, brave, other_ministry
):
    # other_ministry is kind=jovens; make regular_person the general leader of "jovens".
    leadership = MinistryGeneralLeadership.objects.create(kind=MinistryKind.JOVENS)
    leadership.leaders.add(regular_person)
    assert can_manage_ministry(regular_person, other_ministry)
    assert not can_manage_ministry(regular_person, brave)
    assert general_led_kinds(regular_person) == {MinistryKind.JOVENS}


def test_scope_ministries_manager_sees_everything(manager, brave, other_ministry):
    result = set(scope_ministries(Ministry.objects.all(), manager))
    assert result == {brave, other_ministry}


def test_scope_ministries_leader_sees_only_led_ministry(leader, brave, other_ministry):
    result = set(scope_ministries(Ministry.objects.all(), leader))
    assert result == {brave}


def test_scope_ministries_regular_person_sees_nothing(regular_person, brave, other_ministry):
    result = set(scope_ministries(Ministry.objects.all(), regular_person))
    assert result == set()

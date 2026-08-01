import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.churches.choices import ActivityKind, LeadershipRole, MinistryKind
from apps.churches.models import (
    Activity,
    Campus,
    CampusLeadership,
    Church,
    Ministry,
    MinistryLeadership,
    Team,
    TeamLeadership,
)

Person = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def church(db):
    return Church.objects.create(name="Igreja Luz às Nações", abbreviation="ILAN")


@pytest.fixture
def campus(church):
    return Campus.objects.create(church=church, name="Arena ILAN")


@pytest.fixture
def other_campus(church):
    return Campus.objects.create(church=church, name="Outro Campus")


@pytest.fixture
def brave(campus):
    return Ministry.objects.create(
        campus=campus, kind=MinistryKind.ADOLESCENTES, name="Brave", min_age=12, max_age=17
    )


@pytest.fixture
def other_ministry(other_campus):
    return Ministry.objects.create(campus=other_campus, kind=MinistryKind.JOVENS, name="Fogo")


def make_person(campus=None, **kwargs):
    kwargs.setdefault("password", "Senha123!")
    return Person.objects.create_user(campus=campus, **kwargs)


@pytest.fixture
def manager(campus):
    return make_person(email="gestor@ilan.church", name="Gestor", campus=campus, is_manager=True)


@pytest.fixture
def leader(campus, brave):
    person = make_person(email="cesar@ilan.church", name="César", campus=campus)
    MinistryLeadership.objects.create(ministry=brave, person=person, role=LeadershipRole.LEADER)
    return person


@pytest.fixture
def co_leader(campus, brave):
    person = make_person(email="gileade@ilan.church", name="Gileade", campus=campus)
    MinistryLeadership.objects.create(ministry=brave, person=person, role=LeadershipRole.CO_LEADER)
    return person


@pytest.fixture
def regular_person(campus):
    return make_person(email="joao@ilan.church", name="João", campus=campus)


@pytest.fixture
def campus_leader(campus):
    person = make_person(email="leandro@ilan.church", name="Leandro", campus=campus)
    CampusLeadership.objects.create(campus=campus, person=person, role=LeadershipRole.LEADER)
    return person


@pytest.fixture
def campus_co_leader(campus):
    person = make_person(email="ana-paula@ilan.church", name="Ana Paula", campus=campus)
    CampusLeadership.objects.create(campus=campus, person=person, role=LeadershipRole.CO_LEADER)
    return person


@pytest.fixture
def reconciliacao_ministry(campus):
    return Ministry.objects.create(campus=campus, kind=MinistryKind.RECONCILIACAO, name="Reconciliação")


@pytest.fixture
def reconciliador(campus, reconciliacao_ministry):
    person = make_person(email="reconciliador@ilan.church", name="Reconciliador", campus=campus)
    reconciliacao_ministry.members.add(person)
    return person


@pytest.fixture
def pending_lead(db):
    from apps.reconciliation.models import ReconciliationLead

    return ReconciliationLead.objects.create(
        name="Novo Convertido", phone="11999999999", email="convertido@example.com", dedup_key="dedup-1"
    )


@pytest.fixture
def team(brave):
    team = Team.objects.create(ministry=brave, name="Equipe Louvor")
    return team


@pytest.fixture
def team_leader(campus, team):
    person = make_person(email="team-leader@ilan.church", name="Líder de Equipe", campus=campus)
    TeamLeadership.objects.create(team=team, person=person, role=LeadershipRole.LEADER)
    team.members.add(person)
    return person


@pytest.fixture
def team_member(campus, team, brave):
    person = make_person(email="team-member@ilan.church", name="Membro de Equipe", campus=campus)
    brave.members.add(person)
    team.members.add(person)
    return person


@pytest.fixture
def daily_activity(team, team_leader):
    return Activity.objects.create(
        team=team, title="Devocional", kind=ActivityKind.DAILY, points=10, created_by=team_leader
    )


@pytest.fixture
def eventual_activity(team, team_leader):
    return Activity.objects.create(
        team=team, title="Retiro", kind=ActivityKind.EVENTUAL, points=50, created_by=team_leader
    )


@pytest.fixture
def jovens_ministry(campus):
    """Ministério de público (cria Eventos) usado pelos testes de apps.production."""
    return Ministry.objects.create(campus=campus, kind=MinistryKind.JOVENS, name="Jovens")


@pytest.fixture
def jovens_leader(campus, jovens_ministry):
    person = make_person(email="lider-jovens@ilan.church", name="Líder de Jovens", campus=campus)
    MinistryLeadership.objects.create(ministry=jovens_ministry, person=person, role=LeadershipRole.LEADER)
    return person


@pytest.fixture
def media_ministry(campus):
    """Ministério técnico/produção (recebe Demand) usado pelos testes de apps.production."""
    return Ministry.objects.create(campus=campus, kind=MinistryKind.MEDIA, name="Mídia")


@pytest.fixture
def media_leader(campus, media_ministry):
    person = make_person(email="lider-midia@ilan.church", name="Líder de Mídia", campus=campus)
    MinistryLeadership.objects.create(ministry=media_ministry, person=person, role=LeadershipRole.LEADER)
    return person


@pytest.fixture
def media_member(campus, media_ministry):
    person = make_person(email="membro-midia@ilan.church", name="Membro de Mídia", campus=campus)
    media_ministry.members.add(person)
    return person

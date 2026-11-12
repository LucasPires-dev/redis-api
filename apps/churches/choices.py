from django.db import models


class MinistryKind(models.TextChoices):
    JOVENS = "jovens", "Jovens"
    CRIANCAS = "criancas", "Crianças"
    ADOLESCENTES = "adolescentes", "Adolescentes"
    MULHERES = "mulheres", "Mulheres"
    HOMENS = "homens", "Homens"
    CASAIS = "casais", "Casais"
    RECONCILIACAO = "reconciliacao", "Reconciliação"
    MEDIA = "media", "Mídia"
    SOM = "som", "Som"
    ILUMINACAO = "iluminacao", "Iluminação"
    PALCO = "palco", "Palco"
    TRANSMISSAO = "transmissao", "Transmissão"


# "Technical"/production ministries — the ones who carry out the production of an
# Event (as opposed to "audience-facing" ministries, which create Events and send
# demands to these). Used by apps.production to validate a Demand's target and by
# the UI to tell the two categories apart, without duplicating the enum above.
PRODUCTION_MINISTRY_KINDS = {
    MinistryKind.MEDIA,
    MinistryKind.SOM,
    MinistryKind.ILUMINACAO,
    MinistryKind.PALCO,
    MinistryKind.TRANSMISSAO,
}


class CellAudience(models.TextChoices):
    """Who a célula is for. Same values as the audience-facing MinistryKinds, plus
    an explicit "todos" — a mixed cell is a choice, not a missing field."""

    TODOS = "todos", "Todos"
    JOVENS = MinistryKind.JOVENS.value, MinistryKind.JOVENS.label
    CRIANCAS = MinistryKind.CRIANCAS.value, MinistryKind.CRIANCAS.label
    ADOLESCENTES = MinistryKind.ADOLESCENTES.value, MinistryKind.ADOLESCENTES.label
    MULHERES = MinistryKind.MULHERES.value, MinistryKind.MULHERES.label
    HOMENS = MinistryKind.HOMENS.value, MinistryKind.HOMENS.label
    CASAIS = MinistryKind.CASAIS.value, MinistryKind.CASAIS.label


class LeadershipRole(models.TextChoices):
    LEADER = "leader", "Líder"
    CO_LEADER = "co_leader", "Colíder"


class CellRole(models.TextChoices):
    """Roles in a célula. Like LeadershipRole plus the leader in training
    ("Timóteo"), who is being prepared to lead the cell born from a
    multiplication. Kept apart so the extra role doesn't reach ministries."""

    LEADER = "leader", "Líder"
    CO_LEADER = "co_leader", "Colíder"
    TRAINEE = "trainee", "Líder em treinamento"


class ActivityKind(models.TextChoices):
    DAILY = "daily", "Diária"
    EVENTUAL = "eventual", "Eventual"


class VisitorStatus(models.TextChoices):
    """Where a cell visitor stands: still visiting, joined the cell, or handed
    over to the Reconciliação ministry for follow-up (consolidação)."""

    VISITING = "visiting", "Visitante"
    MEMBER = "member", "Virou membro"
    FORWARDED = "forwarded", "Encaminhado à Reconciliação"


class Weekday(models.IntegerChoices):
    """Day of the week, numbered like Python's date.isoweekday() (Monday=1 …
    Sunday=7). Stored as an integer so cells sort by day in calendar order and
    can be filtered with ?meeting_day=3 or ?meeting_day__in=3,5."""

    MONDAY = 1, "Segunda-feira"
    TUESDAY = 2, "Terça-feira"
    WEDNESDAY = 3, "Quarta-feira"
    THURSDAY = 4, "Quinta-feira"
    FRIDAY = 5, "Sexta-feira"
    SATURDAY = 6, "Sábado"
    SUNDAY = 7, "Domingo"

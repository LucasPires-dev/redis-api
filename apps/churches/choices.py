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


# Ministérios "técnicos"/de produção — quem executa a produção de um Evento (ao
# contrário dos ministérios "de público", que criam Eventos e demandam desses).
# Usado pela apps.production para validar o destino de uma Demand e pela UI para
# distinguir as duas categorias, sem duplicar o enum acima.
PRODUCTION_MINISTRY_KINDS = {
    MinistryKind.MEDIA,
    MinistryKind.SOM,
    MinistryKind.ILUMINACAO,
    MinistryKind.PALCO,
    MinistryKind.TRANSMISSAO,
}


class LeadershipRole(models.TextChoices):
    LEADER = "leader", "Líder"
    CO_LEADER = "co_leader", "Colíder"


class ActivityKind(models.TextChoices):
    DAILY = "daily", "Diária"
    EVENTUAL = "eventual", "Eventual"


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

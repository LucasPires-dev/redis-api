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

from django.db import models


class MinistryKind(models.TextChoices):
    JOVENS = "jovens", "Jovens"
    CRIANCAS = "criancas", "Crianças"
    ADOLESCENTES = "adolescentes", "Adolescentes"
    MULHERES = "mulheres", "Mulheres"
    HOMENS = "homens", "Homens"
    CASAIS = "casais", "Casais"


class LeadershipRole(models.TextChoices):
    LEADER = "leader", "Líder"
    CO_LEADER = "co_leader", "Colíder"


class ActivityKind(models.TextChoices):
    DAILY = "daily", "Diária"
    EVENTUAL = "eventual", "Eventual"

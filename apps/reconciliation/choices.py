from django.db import models


class LeadStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    ASSIGNED = "assigned", "Atribuído"
    DISCARDED = "discarded", "Descartado"

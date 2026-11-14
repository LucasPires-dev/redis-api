from django.db import models


class EventType(models.TextChoices):
    CULTO = "culto", "Culto"
    MINISTRY_EVENT = "ministry_event", "Evento de Ministério"
    TRAINING = "training", "Treinamento"
    CONFERENCE = "conference", "Conferência"
    SPECIAL_SERVICE = "special_service", "Culto Especial"


class EventStatus(models.TextChoices):
    PLANNING = "planning", "Planejamento"
    CONFIRMED = "confirmed", "Confirmado"
    IN_PROGRESS = "in_progress", "Em andamento"
    CLOSED = "closed", "Encerrado"


class ItemStatus(models.TextChoices):
    OK = "ok", "Pronto"
    ATTENTION = "attention", "Atenção"
    PROBLEM = "problem", "Problema"


class DemandType(models.TextChoices):
    SLIDE = "slide", "Pedido de slide"
    TECHNICAL_ADJUSTMENT = "technical_adjustment", "Ajuste técnico"
    REPERTOIRE_CHANGE = "repertoire_change", "Mudança de repertório"
    STAGE_SETUP = "stage_setup", "Montagem de palco"
    MATERIAL = "material", "Material"
    OTHER = "other", "Outro"


class DemandPriority(models.TextChoices):
    URGENT = "urgent", "Urgente"
    NORMAL = "normal", "Normal"
    PLANNING = "planning", "Planejamento"


class DemandStatus(models.TextChoices):
    OPEN = "open", "Aberta"
    IN_PROGRESS = "in_progress", "Em andamento"
    DONE = "done", "Concluída"
    REJECTED = "rejected", "Recusada"


class RosterStatus(models.TextChoices):
    INVITED = "invited", "Convidado"
    CONFIRMED = "confirmed", "Confirmado"
    DECLINED = "declined", "Recusado"


class SwapStatus(models.TextChoices):
    PENDING = "pending", "Aguardando"
    ACCEPTED = "accepted", "Aceita"
    DECLINED = "declined", "Recusada"
    CANCELLED = "cancelled", "Cancelada"

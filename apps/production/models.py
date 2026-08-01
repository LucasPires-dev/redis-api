import uuid

from django.conf import settings
from django.db import models

from apps.churches.choices import MinistryKind

from .choices import DemandPriority, DemandStatus, DemandType, EventStatus, EventType, ItemStatus


class Event(models.Model):
    """Generalização de "Culto": tanto o culto regular semanal (criado pela
    Coordenação Geral, campus e ministry_kind nulos) quanto um evento de um
    ministério de público (Retiro de Jovens, Encontro de Mulheres...). campus nulo
    = escopo geral (líder geral do ministério, vale para todos os campi); campus
    preenchido = escopo local (líder local daquele campus)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    event_type = models.CharField(max_length=16, choices=EventType.choices, default=EventType.CULTO)
    campus = models.ForeignKey(
        "churches.Campus", null=True, blank=True, on_delete=models.CASCADE, related_name="production_events"
    )
    ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices, null=True, blank=True)
    starts_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=EventStatus.choices, default=EventStatus.PLANNING)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at"]

    def __str__(self):
        return f"{self.title} ({self.starts_at:%d/%m/%Y %H:%M})"


class RundownItem(models.Model):
    """Um item da ordem do culto/evento, com um ministério principal responsável.
    `order` é a posição na lista (não precisa bater com scheduled_time — permite
    reordenar sem recalcular horários)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="rundown_items")
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    scheduled_time = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["event", "order"], name="unique_order_per_event"),
        ]

    def __str__(self):
        return f"{self.title} ({self.event.title})"


class RundownItemStatus(models.Model):
    """O sinal 🟢🟡🔴 de um ministério para um item específico do rundown — não
    precisa ser o ministry_kind principal do item: qualquer ministério envolvido
    pode reportar seu próprio status (ex: Luz reporta status no item "Louvor 1"
    mesmo o responsável principal sendo Louvor)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rundown_item = models.ForeignKey(RundownItem, on_delete=models.CASCADE, related_name="statuses")
    ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices)
    status = models.CharField(max_length=16, choices=ItemStatus.choices, default=ItemStatus.OK)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rundown_item", "ministry_kind"], name="unique_status_per_ministry"
            ),
        ]

    def __str__(self):
        return f"{self.rundown_item} — {self.ministry_kind}: {self.get_status_display()}"


class Demand(models.Model):
    """Pedido de um ministério a outro (ex: Jovens pede a Mídia que prepare os
    slides do Retiro). `event` é opcional — uma demanda pode ser "geral", sem
    culto/evento específico. from_campus/to_campus registram o campus de cada
    lado no momento da criação (útil quando o solicitante é líder geral, sem um
    campus fixo próprio)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, null=True, blank=True, on_delete=models.CASCADE, related_name="demands")
    from_ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices)
    from_campus = models.ForeignKey(
        "churches.Campus", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    to_ministry_kind = models.CharField(max_length=32, choices=MinistryKind.choices)
    to_campus = models.ForeignKey(
        "churches.Campus", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    demand_type = models.CharField(max_length=32, choices=DemandType.choices)
    priority = models.CharField(max_length=16, choices=DemandPriority.choices, default=DemandPriority.NORMAL)
    description = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=DemandStatus.choices, default=DemandStatus.OPEN)
    rejection_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.from_ministry_kind} → {self.to_ministry_kind}: {self.demand_type} ({self.get_status_display()})"

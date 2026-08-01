import uuid

from django.conf import settings
from django.db import models

from .choices import LeadStatus


class ReconciliationLead(models.Model):
    """One raw row imported from the connected Google Sheet (Google Forms responses
    about a new convert). Disposable staging data — deliberately NOT tied to
    Person/login. A campus leader (or Reconciliação ministry leader) triages it —
    assigning/correcting its campus — then either assigns it to a reconciliador
    (which creates the real Person + Reconciliation, see Reconciliation.source_lead)
    or discards it. Never mutated once assigned/discarded except by that transition."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    raw_campus_label = models.CharField(
        max_length=150,
        blank=True,
        help_text="Texto bruto digitado/selecionado no Google Forms — pode não bater 1:1 com o nome de um Campus interno.",
    )
    campus = models.ForeignKey(
        "churches.Campus",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reconciliation_leads",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    dedup_key = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=16, choices=LeadStatus.choices, default=LeadStatus.PENDING)
    raw_row = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at", "-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class Reconciliation(models.Model):
    """One new convert's follow-up journey through the Ministério de Reconciliação.
    One row per convert (OneToOne). Baptism reuses Person.baptized/baptized_at
    directly; GT reuses Cell.members; serving reuses Ministry.members — none of
    those are duplicated here. `reconciliador` is a plain (non-unique) FK, not a
    leadership row, since re-assigning a convert to a different reconciliador is
    exactly what this field is for (no separate "unassign" state needed)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    convert = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reconciliation"
    )
    reconciliador = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reconciliation_assignments"
    )
    campus = models.ForeignKey(
        "churches.Campus", on_delete=models.PROTECT, related_name="reconciliations"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    jornada_completed = models.BooleanField(default=False)
    jornada_completed_at = models.DateField(null=True, blank=True)
    source_lead = models.OneToOneField(
        ReconciliationLead, null=True, blank=True, on_delete=models.SET_NULL, related_name="reconciliation"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-assigned_at"]

    def __str__(self):
        return f"{self.convert} — reconciliador {self.reconciliador}"

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.reconciliation.choices import LeadStatus
from apps.reconciliation.models import ReconciliationLead
from apps.reconciliation.sheets import compute_dedup_key, fetch_rows, parse_submitted_at


class Command(BaseCommand):
    help = "Pulls new-convert rows from the connected Google Sheet into ReconciliationLead (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        created = updated = skipped = 0
        for row in fetch_rows():
            name = (row.get("name") or "").strip()
            if not name:
                skipped += 1
                continue
            submitted_at = parse_submitted_at(row.get("submitted_at"))
            email = (row.get("email") or "").strip()
            phone = (row.get("phone") or "").strip()
            dedup_key = compute_dedup_key(email, phone, submitted_at)

            lead, was_created = ReconciliationLead.objects.get_or_create(
                dedup_key=dedup_key,
                defaults={
                    "name": name,
                    "phone": phone,
                    "email": email,
                    "raw_campus_label": row.get("raw_campus_label", ""),
                    "submitted_at": submitted_at,
                    "raw_row": row.get("raw_row", {}),
                },
            )
            if was_created:
                created += 1
            elif lead.status == LeadStatus.PENDING:
                # Only refresh fields while the lead is still pending — never clobber
                # one already triaged/assigned/discarded by a later sync re-reading
                # an edited sheet row.
                lead.name, lead.phone, lead.email = name, phone, email
                lead.raw_campus_label = row.get("raw_campus_label", "")
                lead.raw_row = row.get("raw_row", {})
                lead.save(update_fields=["name", "phone", "email", "raw_campus_label", "raw_row", "updated_at"])
                updated += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Sync concluído: {created} novos, {updated} atualizados (pendentes), {skipped} ignorados."
            )
        )

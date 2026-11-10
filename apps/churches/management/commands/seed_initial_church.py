from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.churches.choices import LeadershipRole, MinistryKind
from apps.churches.models import Campus, CampusLeadership, Church, Ministry, MinistryLeadership

Person = get_user_model()

TEMP_PASSWORD = "TrocarSenha123!"


class Command(BaseCommand):
    help = "Seeds the real initial data for ILAN: church, campus, the Brave ministry and its leaders."

    @transaction.atomic
    def handle(self, *args, **options):
        church, _ = Church.objects.get_or_create(
            abbreviation="ILAN", defaults={"name": "Igreja Luz às Nações"}
        )

        campus, _ = Campus.objects.get_or_create(church=church, name="Arena ILAN")

        ministry, _ = Ministry.objects.get_or_create(
            campus=campus,
            name="Brave",
            defaults={
                "kind": MinistryKind.ADOLESCENTES,
                "description": "Ministério de adolescentes de 12 a 17 anos.",
                "min_age": 12,
                "max_age": 17,
            },
        )

        manager, created = Person.objects.get_or_create(
            email="gestor@ilan.church",
            defaults={"name": "Gestor ILAN", "campus": campus, "is_manager": True, "is_staff": True},
        )
        if created:
            manager.set_password(TEMP_PASSWORD)
            manager.save(update_fields=["password"])

        leaders = [
            ("lider.brave1@ilan.church", "Líder Brave 1", LeadershipRole.LEADER),
            ("lider.brave2@ilan.church", "Líder Brave 2", LeadershipRole.LEADER),
            ("colider.brave1@ilan.church", "Co-líder Brave 1", LeadershipRole.CO_LEADER),
            ("colider.brave2@ilan.church", "Co-líder Brave 2", LeadershipRole.CO_LEADER),
        ]
        for email, name, role in leaders:
            person, created = Person.objects.get_or_create(
                email=email, defaults={"name": name, "campus": campus}
            )
            if created:
                person.set_password(TEMP_PASSWORD)
                person.save(update_fields=["password"])
            MinistryLeadership.objects.update_or_create(
                ministry=ministry, person=person, defaults={"role": role}
            )

        campus_leaders = [
            ("lider.campus1@ilan.church", "Líder Campus 1"),
            ("lider.campus2@ilan.church", "Líder Campus 2"),
        ]
        for email, name in campus_leaders:
            person, created = Person.objects.get_or_create(
                email=email, defaults={"name": name, "campus": campus}
            )
            if created:
                person.set_password(TEMP_PASSWORD)
                person.save(update_fields=["password"])
            # Both are campus leaders (role=leader) — no hierarchy between them, same
            # access level, confirmed with the user rather than assumed.
            CampusLeadership.objects.update_or_create(
                campus=campus, person=person, defaults={"role": LeadershipRole.LEADER}
            )

        reconciliacao_ministry, _ = Ministry.objects.get_or_create(
            campus=campus,
            kind=MinistryKind.RECONCILIACAO,
            defaults={"name": "Reconciliação", "description": "Acompanhamento de novos convertidos."},
        )
        reconciliation_leader, created = Person.objects.get_or_create(
            email="lider.reconciliacao@ilan.church",
            defaults={"name": "Líder Reconciliação", "campus": campus},
        )
        if created:
            reconciliation_leader.set_password(TEMP_PASSWORD)
            reconciliation_leader.save(update_fields=["password"])
        MinistryLeadership.objects.update_or_create(
            ministry=reconciliacao_ministry,
            person=reconciliation_leader,
            defaults={"role": LeadershipRole.LEADER},
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {church} / {campus} / {ministry} with {len(leaders)} ministry leaders, "
                f"{len(campus_leaders)} campus leaders, "
                f"{reconciliacao_ministry} with 1 leader, + 1 manager.\n"
                f"Placeholder password for every seeded account: {TEMP_PASSWORD} — change it via /admin/ or the API."
            )
        )

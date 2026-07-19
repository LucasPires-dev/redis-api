from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.churches.choices import LeadershipRole, MinistryKind
from apps.churches.models import Campus, Church, Ministry, MinistryLeadership

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
            ("cesar.augusto@ilan.church", "César Augusto", LeadershipRole.LEADER),
            ("natasha.augusto@ilan.church", "Natasha Augusto", LeadershipRole.LEADER),
            ("gileade.bart@ilan.church", "Gileade Bart", LeadershipRole.CO_LEADER),
            ("gabriela.bart@ilan.church", "Gabriela Bart", LeadershipRole.CO_LEADER),
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

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {church} / {campus} / {ministry} with {len(leaders)} leaders + 1 manager.\n"
                f"Placeholder password for every seeded account: {TEMP_PASSWORD} — change it via /admin/ or the API."
            )
        )

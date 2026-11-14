from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.churches.choices import LeadershipRole, MinistryKind
from apps.churches.models import (
    Campus,
    CampusCellsLeadership,
    CampusLeadership,
    CellGeneralLeadership,
    Church,
    Ministry,
    MinistryGeneralLeadership,
    MinistryLeadership,
)
from apps.courses.models import CampusCoursesLeadership, CourseGeneralLeadership
from apps.production.models import EventProducerAssignment

Person = get_user_model()

TEMP_PASSWORD = "TrocarSenha123!"


class Command(BaseCommand):
    help = (
        "Seeds the initial data for ILAN: church, campus, ministries and one account "
        "per access level (manager, general and local responsibles, plain member)."
    )

    def _account(self, campus, email, name, **extra):
        """Gets or creates a seeded person. The password is only set on creation,
        so re-running the seed never resets a password someone already changed."""
        person, created = Person.objects.get_or_create(
            email=email, defaults={"name": name, "campus": campus, **extra}
        )
        if created:
            person.set_password(TEMP_PASSWORD)
            person.save(update_fields=["password"])
        self.accounts.append((email, name))
        return person

    @transaction.atomic
    def handle(self, *args, **options):
        self.accounts = []

        church, _ = Church.objects.get_or_create(
            abbreviation="ILAN", defaults={"name": "Igreja Luz às Nações"}
        )
        campus, _ = Campus.objects.get_or_create(church=church, name="Arena ILAN")

        # --- Institution level -----------------------------------------------
        # The manager sees and manages everything and appoints the general
        # responsibles below.
        self._account(
            campus, "gestor@ilan.church", "Gestor ILAN", is_manager=True, is_staff=True
        )

        # --- Campus level ------------------------------------------------------
        # Both are campus leaders (role=leader) — no hierarchy between them, same
        # access level, confirmed with the user rather than assumed.
        for email, name in [
            ("lider.campus1@ilan.church", "Líder Campus 1"),
            ("lider.campus2@ilan.church", "Líder Campus 2"),
        ]:
            CampusLeadership.objects.update_or_create(
                campus=campus,
                person=self._account(campus, email, name),
                defaults={"role": LeadershipRole.LEADER},
            )

        # --- Ministries (local leaders) ---------------------------------------
        brave, _ = Ministry.objects.get_or_create(
            campus=campus,
            name="Brave",
            defaults={
                "kind": MinistryKind.ADOLESCENTES,
                "description": "Ministério de adolescentes de 12 a 17 anos.",
                "min_age": 12,
                "max_age": 17,
            },
        )
        for email, name, role in [
            ("lider.brave1@ilan.church", "Líder Brave 1", LeadershipRole.LEADER),
            ("lider.brave2@ilan.church", "Líder Brave 2", LeadershipRole.LEADER),
            ("colider.brave1@ilan.church", "Co-líder Brave 1", LeadershipRole.CO_LEADER),
            ("colider.brave2@ilan.church", "Co-líder Brave 2", LeadershipRole.CO_LEADER),
        ]:
            MinistryLeadership.objects.update_or_create(
                ministry=brave, person=self._account(campus, email, name), defaults={"role": role}
            )

        reconciliacao, _ = Ministry.objects.get_or_create(
            campus=campus,
            kind=MinistryKind.RECONCILIACAO,
            defaults={"name": "Reconciliação", "description": "Acompanhamento de novos convertidos."},
        )
        MinistryLeadership.objects.update_or_create(
            ministry=reconciliacao,
            person=self._account(campus, "lider.reconciliacao@ilan.church", "Líder Reconciliação"),
            defaults={"role": LeadershipRole.LEADER},
        )

        # --- General responsibles (every campus) ------------------------------
        # Chosen by the manager. Each manages their area on every campus and
        # appoints its local responsibles.
        ministry_general, _ = MinistryGeneralLeadership.objects.get_or_create(
            kind=MinistryKind.ADOLESCENTES
        )
        ministry_general.leaders.add(
            self._account(campus, "geral.adolescentes@ilan.church", "Responsável Geral Adolescentes")
        )

        cells_general, _ = CellGeneralLeadership.objects.get_or_create(church=church)
        cells_general.leaders.add(
            self._account(campus, "geral.celulas@ilan.church", "Responsável Geral Células")
        )

        courses_general, _ = CourseGeneralLeadership.objects.get_or_create(church=church)
        courses_general.leaders.add(
            self._account(campus, "geral.cursos@ilan.church", "Responsável Geral Cursos")
        )

        EventProducerAssignment.objects.get_or_create(
            person=self._account(campus, "geral.eventos@ilan.church", "Produtor Geral de Eventos"),
            campus=None,  # null = church-wide
        )

        # --- Local responsibles (this campus only) ----------------------------
        # Appointed by the campus leader or by the area's general responsible.
        CampusCellsLeadership.objects.update_or_create(
            campus=campus,
            person=self._account(campus, "celulas.arena@ilan.church", "Responsável Células Arena"),
            defaults={"role": LeadershipRole.LEADER},
        )
        CampusCoursesLeadership.objects.update_or_create(
            campus=campus,
            person=self._account(campus, "cursos.arena@ilan.church", "Responsável Cursos Arena"),
            defaults={"role": LeadershipRole.LEADER},
        )
        EventProducerAssignment.objects.get_or_create(
            person=self._account(campus, "eventos.arena@ilan.church", "Produtor de Eventos Arena"),
            campus=campus,
        )

        # --- No role ------------------------------------------------------------
        # To check what a regular member can (and can't) see.
        self._account(campus, "membro@ilan.church", "Membro ILAN")

        lines = "\n".join(f"  {email:<36} {name}" for email, name in self.accounts)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {church} / {campus} with {len(self.accounts)} accounts:\n{lines}\n"
                f"Placeholder password for every seeded account: {TEMP_PASSWORD} "
                "— change it via /admin/ or the API."
            )
        )

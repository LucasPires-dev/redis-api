"""Appointments notify the appointed person.

Responsibilities are given in many places (screens, Django Admin, the seed), so
they are caught where they are stored: a new leadership row (post_save) or a
new person in a general leadership (m2m_changed), instead of in every view.
"""

from django.db.models.signals import m2m_changed, post_save

from apps.churches.models import (
    CampusCellsLeadership,
    CampusLeadership,
    CellGeneralLeadership,
    CellLeadership,
    MinistryGeneralLeadership,
    MinistryLeadership,
    TeamLeadership,
)
from apps.courses.choices import TeacherRole
from apps.courses.models import CampusCoursesLeadership, CourseClassTeacher, CourseGeneralLeadership

from . import triggers

TEACHER_ROLE = {TeacherRole.TEACHER: "professor(a)", TeacherRole.ASSISTANT: "auxiliar"}


def _role(leadership) -> str:
    return leadership.get_role_display().lower()


def _created(model):
    """Registers `handler(instance)` for new rows of `model` only."""

    def decorator(handler):
        def receiver_fn(sender, instance, created, raw=False, **kwargs):
            if created and not raw:
                handler(instance)

        post_save.connect(receiver_fn, sender=model, weak=False, dispatch_uid=f"notify-{model.__name__}")
        return handler

    return decorator


@_created(CampusLeadership)
def _campus(lead):
    triggers.appointed(
        lead.person_id, _role(lead), f"do campus {lead.campus.name}", f"/app/campus/{lead.campus_id}",
        f"campus:{lead.pk}",
    )


@_created(MinistryLeadership)
def _ministry(lead):
    ministry = lead.ministry
    triggers.appointed(
        lead.person_id, _role(lead), f"do ministério {ministry.name}",
        f"/app/ministerios/{ministry.kind}/{ministry.campus_id}", f"ministry:{lead.pk}",
    )


@_created(TeamLeadership)
def _team(lead):
    team = lead.team
    triggers.appointed(
        lead.person_id, _role(lead), f"da equipe {team.name}",
        f"/app/ministerios/{team.ministry.kind}/{team.ministry.campus_id}/equipes/{team.pk}",
        f"team:{lead.pk}",
    )


@_created(CellLeadership)
def _cell(lead):
    triggers.appointed(
        lead.person_id, _role(lead), f"da célula {lead.cell.name}", f"/app/celulas/{lead.cell_id}",
        f"cell:{lead.pk}",
    )


@_created(CampusCellsLeadership)
def _campus_cells(lead):
    triggers.appointed(
        lead.person_id, _role(lead), f"das células do campus {lead.campus.name}", "/app/celulas/coordenacao",
        f"campus-cells:{lead.pk}",
    )


@_created(CampusCoursesLeadership)
def _campus_courses(lead):
    triggers.appointed(
        lead.person_id, _role(lead), f"dos cursos do campus {lead.campus.name}", "/app/cursos/gestao",
        f"campus-courses:{lead.pk}",
    )


@_created(CourseClassTeacher)
def _teacher(assignment):
    cc = assignment.course_class
    triggers.appointed(
        assignment.person_id,
        TEACHER_ROLE.get(assignment.role, "professor(a)"),
        f"da turma {cc.name} ({cc.course.name})",
        f"/app/cursos/{cc.course_id}/turma/{cc.pk}",
        f"teacher:{assignment.pk}",
    )


# ── General leaderships (many-to-many "leaders") ─────────────────────────────


def _general(owner_model, describe):
    """Notifies people added to `owner_model.leaders`, from either side."""

    def receiver_fn(sender, instance, action, reverse, pk_set, **kwargs):
        if action != "post_add" or not pk_set:
            return
        if reverse:  # person.<related>.add(leadership, ...)
            for owner in owner_model.objects.filter(pk__in=pk_set):
                triggers.appointed([instance.pk], *describe(owner))
        else:
            triggers.appointed(list(pk_set), *describe(instance))

    m2m_changed.connect(
        receiver_fn, sender=owner_model.leaders.through, weak=False, dispatch_uid=f"notify-{owner_model.__name__}"
    )


_general(
    MinistryGeneralLeadership,
    lambda g: ("líder geral", f"de {g.get_kind_display()}", f"/app/ministerios/{g.kind}"),
)
_general(CellGeneralLeadership, lambda g: ("responsável geral", "pelas células", "/app/celulas/coordenacao"))
_general(CourseGeneralLeadership, lambda g: ("responsável geral", "pelos cursos", "/app/cursos/gestao"))

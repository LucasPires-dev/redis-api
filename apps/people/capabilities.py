"""What each area of the app lets the person do, as independent flags.

The frontend builds every area screen (Células, Cursos, Eventos) from these:
personal tabs are always there, and each management tab appears when its flag
is true. The flags are computed by the same permission functions the API uses,
so a tab shows up exactly when the API will accept its actions.

Flags never exclude each other: a cell leader who also coordinates gets both
`lead` and `coordinate`; a coordinator taking a course still gets the personal
tabs (they don't depend on any flag).
"""

from django.db.models import Q


def cells_capabilities(person) -> dict:
    from apps.churches.models import Campus, Cell
    from apps.churches.permissions import can_coordinate_cells

    coordinate = can_coordinate_cells(person) or Campus.objects.filter(
        Q(leaderships__person=person) | Q(cells_leaderships__person=person)
    ).exists()
    return {
        # Leads or co-leads at least one cell ("Minha célula" with the leader tools).
        "lead": Cell.objects.filter(leaderships__person=person).exists(),
        # Church-wide, campus leader or campus cells leader (can_coordinate_campus_cells).
        "coordinate": coordinate,
        # Supervises at least one cell (the "Supervisão" tab).
        "supervise": Cell.objects.filter(supervisor=person).exists(),
    }


def courses_capabilities(person) -> dict:
    from apps.courses.models import CourseClassTeacher
    from apps.courses.permissions import can_coordinate_courses, coordinated_course_campuses

    return {
        # Teaches (or assists) at least one class: "Minhas turmas".
        "teach": CourseClassTeacher.objects.filter(person=person).exists(),
        # Church-wide or local coordination: "Gestão".
        "coordinate": can_coordinate_courses(person) or coordinated_course_campuses(person).exists(),
        # Creates church-wide courses (campus = null).
        "coordinate_all": can_coordinate_courses(person),
    }


def events_capabilities(person) -> dict:
    from apps.churches.models import Campus, Ministry
    from apps.churches.permissions import general_led_kinds
    from apps.production.models import EventProducerAssignment

    is_producer = EventProducerAssignment.objects.filter(person=person).exists()
    leads_campus = Campus.objects.filter(leaderships__person=person).exists()
    leads_ministry = (
        Ministry.objects.filter(leaderships__person=person).exists() or bool(general_led_kinds(person))
    )
    serves_ministry = Ministry.objects.filter(members=person).exists()
    # Who can create some kind of event (see can_create_event).
    create = person.is_manager or is_producer or leads_campus or leads_ministry
    return {
        "create": create,
        # Manager or event producer: tickets and attendee lists of the events they produce.
        "produce": person.is_manager or is_producer,
        # Backstage work ("Produção" and "Demandas"): whoever creates events, plus
        # ministry members (rundown status, checklist, roster confirmation).
        "production": create or serves_ministry,
    }


def capabilities(person) -> dict:
    if not person.is_authenticated:
        return {}
    return {
        "cells": cells_capabilities(person),
        "courses": courses_capabilities(person),
        "events": events_capabilities(person),
    }

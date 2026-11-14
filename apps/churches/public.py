"""What anyone may see without logging in: the cells, course classes and events
a church opened to everyone ("Todos os públicos").

A public cell shows only its church, weekday and time: no name, address or
people (they meet in a home). The rest reaches the person only after the
cell's leadership accepts them."""

from datetime import timedelta

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .choices import Visibility
from .models import Cell, Church

# Enough for a page of each kind; the list is a showcase, not a search.
LIMIT = 60


def _church(church):
    return {"id": str(church.pk), "name": church.name} if church else None


def _campus(campus):
    return {"name": campus.name, "city": campus.city} if campus else None


def cell_data(cell):
    return {
        "id": str(cell.pk),
        "church": _church(cell.campus.church),
        "meeting_day": cell.meeting_day,
        "meeting_day_display": cell.get_meeting_day_display(),
        "meeting_time": cell.meeting_time.strftime("%H:%M") if cell.meeting_time else None,
    }


def class_data(course_class):
    course = course_class.course
    return {
        "id": str(course_class.pk),
        "course_name": course.name,
        "course_description": course.description,
        "name": course_class.name,
        "description": course_class.description,
        "church": _church(course.church),
        "campus": _campus(course_class.campus),
        "weekday": course_class.weekday,
        "start_time": course_class.start_time.strftime("%H:%M") if course_class.start_time else None,
        "start_date": course_class.start_date,
        "end_date": course_class.end_date,
        "enrollment_deadline": course_class.enrollment_deadline,
        "base_price_cents": course_class.base_price_cents,
    }


def event_data(event):
    from apps.payments.models import EventTicketConfig

    try:
        config = event.ticket_config
    except EventTicketConfig.DoesNotExist:
        config = None
    registration = None
    if config is not None:
        prices = [t.price_cents for t in config.types.all()] or [config.price_cents]
        registration = {"price_from_cents": min(prices)}
    return {
        "id": str(event.pk),
        "title": event.title,
        "starts_at": event.starts_at,
        "ends_at": event.ends_at,
        "church": _church(event.church),
        "campus": _campus(event.campus),
        "registration": registration,
    }


def public_cells():
    return (
        Cell.objects.active()
        .filter(visibility=Visibility.PUBLIC)
        .select_related("campus__church")
        .order_by("meeting_day", "meeting_time")
    )


def public_classes():
    from apps.courses.billing import open_classes_filter
    from apps.courses.models import CourseClass

    return (
        CourseClass.objects.filter(open_classes_filter(), visibility=Visibility.PUBLIC)
        .select_related("course__church", "campus")
        .prefetch_related("fee_items")
        .order_by("start_date", "course__name", "name")
    )


def public_events():
    from apps.production.choices import EventStatus
    from apps.production.models import Event

    now = timezone.now()
    return (
        Event.objects.filter(visibility=Visibility.PUBLIC)
        .filter(Q(ends_at__gte=now) | Q(ends_at__isnull=True, starts_at__gte=now - timedelta(hours=6)))
        .exclude(status=EventStatus.CLOSED)
        .select_related("church", "campus", "ticket_config")
        .prefetch_related("ticket_config__types")
        .order_by("starts_at")
    )


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def explore_view(request):
    """Everything open to everyone, optionally from one church (?church=)."""
    cells, classes, events = public_cells(), public_classes(), public_events()
    church_id = request.query_params.get("church")
    if church_id:
        cells = cells.filter(campus__church_id=church_id)
        classes = classes.filter(course__church_id=church_id)
        events = events.filter(church_id=church_id)
    churches = Church.objects.filter(
        Q(campuses__cells__in=public_cells())
        | Q(courses__classes__in=public_classes())
        | Q(events__in=public_events())
    ).distinct().order_by("name")
    return Response(
        {
            "churches": [_church(c) for c in churches],
            "events": [event_data(e) for e in events[:LIMIT]],
            "classes": [class_data(c) for c in classes[:LIMIT]],
            "cells": [cell_data(c) for c in cells[:LIMIT]],
        }
    )


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def public_cell_view(request, cell_id):
    return Response(cell_data(get_object_or_404(public_cells(), pk=cell_id)))


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def public_class_view(request, class_id):
    return Response(class_data(get_object_or_404(public_classes(), pk=class_id)))

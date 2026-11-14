"""Duplicating events and generating the occurrences of recurring ones.

`duplicate_event` copies an event to another date: rundown (times shifted),
checklist (not done), ticket and its types (dates shifted) and, when asked,
the roster (as new invitations). A recurring series (EventSeries) creates each
occurrence that way from its template, `weeks_ahead` weeks ahead; the daily
task keeps the window filled.
"""

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.notifications import triggers

from .choices import EventStatus, RosterStatus
from .models import Event, EventChecklistItem, EventRosterEntry, EventSeries, RundownItem


def _shift(value, delta):
    return value + delta if value else value


def duplicate_event(source, starts_at, *, by, title=None, copy_roster=True, series=None, **overrides):
    from apps.payments.models import EventTicketConfig, EventTicketType  # payments depends on production

    delta = starts_at - source.starts_at
    with transaction.atomic():
        event = Event.objects.create(
            title=title or source.title,
            description=source.description,
            event_type=source.event_type,
            campus=source.campus,
            ministry_kind=source.ministry_kind,
            starts_at=starts_at,
            ends_at=_shift(source.ends_at, delta),
            status=EventStatus.PLANNING,
            producer=source.producer,
            created_by=by,
            series=series,
            **overrides,
        )
        RundownItem.objects.bulk_create(
            RundownItem(
                event=event,
                order=item.order,
                title=item.title,
                scheduled_time=_shift(item.scheduled_time, delta),
                duration_minutes=item.duration_minutes,
                ministry_kind=item.ministry_kind,
                notes=item.notes,
            )
            for item in source.rundown_items.all()
        )
        EventChecklistItem.objects.bulk_create(
            EventChecklistItem(event=event, ministry_kind=item.ministry_kind, text=item.text, created_by=by)
            for item in source.checklist_items.all()
        )
        config = EventTicketConfig.objects.filter(event=source).first()
        if config is not None:
            new_config = EventTicketConfig.objects.create(
                event=event,
                price_cents=config.price_cents,
                max_capacity=config.max_capacity,
                registration_deadline=_shift(config.registration_deadline, delta),
                description=config.description,
                max_companions=config.max_companions,
                is_public=config.is_public,
            )
            EventTicketType.objects.bulk_create(
                EventTicketType(
                    config=new_config,
                    name=t.name,
                    price_cents=t.price_cents,
                    capacity=t.capacity,
                    sales_start=_shift(t.sales_start, delta),
                    sales_end=_shift(t.sales_end, delta),
                    order=t.order,
                )
                for t in config.types.all()
            )
        entries = []
        if copy_roster:
            for entry in source.roster_entries.exclude(status=RosterStatus.DECLINED):
                entries.append(
                    EventRosterEntry.objects.create(
                        event=event,
                        ministry_id=entry.ministry_id,
                        person_id=entry.person_id,
                        function=entry.function,
                        invited_by=by,
                    )
                )
    for entry in entries:
        triggers.roster_invited(entry)
    return event


# ── Recurring events ─────────────────────────────────────────────────────────


def occurrences(series, start, end):
    """The series' dates between `start` and `end` (inclusive)."""
    first = series.starts_on + timedelta(days=(series.weekday - series.starts_on.weekday()) % 7)
    step = timedelta(weeks=max(1, series.every_weeks))
    skipped = set(series.skipped_dates or [])
    if first < start:
        # Jump to the first occurrence on or after `start`, keeping the rhythm.
        steps = (start - first).days // step.days
        first += step * steps
        if first < start:
            first += step
    day = first
    while day <= end and (series.ends_on is None or day <= series.ends_on):
        if day.isoformat() not in skipped:
            yield day
        day += step


def _starts_at(series, day):
    return timezone.make_aware(datetime.combine(day, series.start_time))


def generate(series, today=None):
    """Creates the missing occurrences in the window ahead. Returns them."""
    if not series.is_active:
        return []
    today = today or timezone.localdate()
    now = timezone.now()
    existing = {timezone.localdate(e.starts_at) for e in series.events.only("starts_at")}
    created = []
    for day in occurrences(series, today, today + timedelta(weeks=series.weeks_ahead)):
        starts_at = _starts_at(series, day)
        if day in existing or starts_at <= now:
            continue
        common = {"title": series.title, "series": series}
        if series.template_id:
            event = duplicate_event(
                series.template, starts_at, by=series.created_by, copy_roster=series.copy_roster, **common
            )
            event.ends_at = starts_at + timedelta(minutes=series.duration_minutes)
            event.description = series.description or event.description
            event.save(update_fields=["ends_at", "description"])
        else:
            event = Event.objects.create(
                description=series.description,
                event_type=series.event_type,
                campus=series.campus,
                ministry_kind=series.ministry_kind,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=series.duration_minutes),
                created_by=series.created_by,
                **common,
            )
        created.append(event)
    return created


def generate_all(today=None):
    total = 0
    for series in EventSeries.objects.filter(is_active=True).select_related("template", "created_by"):
        total += len(generate(series, today))
    return total


def skip(event):
    """An occurrence deleted on purpose is not created again."""
    if not event.series_id:
        return
    series = event.series
    day = timezone.localdate(event.starts_at).isoformat()
    if day not in series.skipped_dates:
        series.skipped_dates = [*series.skipped_dates, day]
        series.save(update_fields=["skipped_dates", "updated_at"])


def remove_future(series):
    """Ending a series: its upcoming occurrences go too, except those with
    registrations (people already signed up)."""
    upcoming = series.events.filter(starts_at__gt=timezone.now(), registrations__isnull=True)
    count = upcoming.count()
    upcoming.delete()
    return count

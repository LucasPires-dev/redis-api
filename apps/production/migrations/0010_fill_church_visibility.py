"""Data: the church of each event and the public events (before the ticket's
is_public is removed by payments 0006)."""

from django.db import migrations


def fill(apps, schema_editor):
    """Each event gets its church (the campus's; for a general event, its
    creator's, or the only church), and the events that had a public link
    become public."""
    Event = apps.get_model("production", "Event")
    Church = apps.get_model("churches", "Church")
    Config = apps.get_model("payments", "EventTicketConfig")
    churches = list(Church.objects.values_list("pk", flat=True)[:2])
    only = churches[0] if len(churches) == 1 else None
    for event in Event.objects.select_related("campus", "created_by__campus"):
        if event.campus_id:
            church = event.campus.church_id
        elif event.created_by_id and event.created_by.campus_id:
            church = event.created_by.campus.church_id
        else:
            church = only
        if church:
            Event.objects.filter(pk=event.pk).update(church_id=church)
    public = Config.objects.filter(is_public=True).values_list("event_id", flat=True)
    Event.objects.filter(pk__in=list(public)).update(visibility="public")


class Migration(migrations.Migration):

    dependencies = [
        ("production", "0009_visibility"),
        ("payments", "0005_reports"),
    ]

    operations = [
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]

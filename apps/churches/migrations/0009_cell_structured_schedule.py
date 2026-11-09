"""Replace the free-text Cell.weekday with a structured meeting day + time.

The old `weekday` was a CharField holding things like "Quarta", "quarta-feira",
"Qua 19h30". It is replaced by `meeting_day` (integer, choices.Weekday,
1=Monday … 7=Sunday) and `meeting_time` (TimeField).

Steps: add the new columns, parse the old text into them, drop the old column.
Any text that can't be parsed is appended to the cell's description so nothing
is lost.
"""

import re
import unicodedata
from datetime import time

from django.db import migrations, models

# Prefix of the normalized (lowercase, no accents) day name -> ISO weekday.
DAY_PREFIXES = [
    ("seg", 1),
    ("ter", 2),
    ("qua", 3),
    ("qui", 4),
    ("sex", 5),
    ("sab", 6),
    ("dom", 7),
]

# Matches "19h", "19h30", "19:30", "19 h 30".
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])\s*(?:h|:)\s*([0-5]\d)?\b")

LABELS = {
    1: "Segunda-feira",
    2: "Terça-feira",
    3: "Quarta-feira",
    4: "Quinta-feira",
    5: "Sexta-feira",
    6: "Sábado",
    7: "Domingo",
}


def _normalize(text):
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def parse_schedule(raw):
    """Return (meeting_day | None, meeting_time | None) parsed from the old free text."""
    text = _normalize(raw)
    meeting_day = None
    for word in re.findall(r"[a-z]+", text):
        for prefix, number in DAY_PREFIXES:
            if word.startswith(prefix):
                meeting_day = number
                break
        if meeting_day:
            break
    meeting_time = None
    match = TIME_RE.search(text)
    if match:
        meeting_time = time(int(match.group(1)), int(match.group(2) or 0))
    return meeting_day, meeting_time


def forwards(apps, schema_editor):
    Cell = apps.get_model("churches", "Cell")
    for cell in Cell.objects.exclude(weekday=""):
        cell.meeting_day, cell.meeting_time = parse_schedule(cell.weekday)
        if cell.meeting_day is None:
            # Keep the original text visible instead of silently dropping it.
            note = f"Dia/horário (texto antigo): {cell.weekday}"
            cell.description = f"{cell.description}\n\n{note}".strip()
        cell.save(update_fields=["meeting_day", "meeting_time", "description"])


def backwards(apps, schema_editor):
    Cell = apps.get_model("churches", "Cell")
    for cell in Cell.objects.exclude(meeting_day=None):
        text = LABELS[cell.meeting_day]
        if cell.meeting_time:
            text = f"{text[:3]} {cell.meeting_time:%Hh%M}"
        cell.weekday = text[:20]
        cell.save(update_fields=["weekday"])


class Migration(migrations.Migration):

    dependencies = [
        ("churches", "0008_alter_cell_target_audience_alter_ministry_kind_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="cell",
            name="meeting_day",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                choices=[
                    (1, "Segunda-feira"),
                    (2, "Terça-feira"),
                    (3, "Quarta-feira"),
                    (4, "Quinta-feira"),
                    (5, "Sexta-feira"),
                    (6, "Sábado"),
                    (7, "Domingo"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="cell",
            name="meeting_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(model_name="cell", name="weekday"),
        migrations.AddIndex(
            model_name="cell",
            index=models.Index(
                fields=["campus", "meeting_day", "meeting_time"], name="cell_schedule_idx"
            ),
        ),
    ]

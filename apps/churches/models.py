"""Models for the church structure and the activities/points system.

Hierarchy:

    Church
    └── Campus
        ├── Ministry (Jovens, Mídia, Reconciliação, ...)
        │   └── Team (working group inside the ministry)
        │       ├── Activity (task worth points)
        │       │   └── ActivityCompletion (a person completed it on a given day)
        │       └── TeamPointAward (points given to the whole team)
        └── Cell (célula/GT, tied directly to the campus and to a Season)
            └── CellMeeting (one meeting, with who attended)

Season: a period (e.g. "2026.2") defined church-wide. Cells run per season:
each season has its own Cell rows, and a cell carried over to the next season
is a *new* Cell pointing back to the old one (`previous_cell`), so members,
leaders and meetings of past seasons stay intact as history.

Leaderships: Campus, Ministry, Cell and Team each have a *Leadership model
(CampusLeadership, MinistryLeadership, ...). Each row links a person to the
group with the role of leader or co-leader (LeadershipRole). Leadership lives in
its own table, not in a field on Person, so it can be reassigned at any time
without touching the person's record. Leader and co-leader have the same access
level (see apps/churches/permissions.py).

Conventions shared by every model:
- `id` is a UUID (doesn't reveal record counts and isn't guessable).
- `created_at` / `updated_at` are filled in automatically.
- References to people use settings.AUTH_USER_MODEL (which is people.Person).
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .choices import ActivityKind, LeadershipRole, MinistryKind, Weekday

# =============================================================================
# Structure: church and campus
# =============================================================================


class Church(models.Model):
    """The church (e.g. Mega Igreja). Top of the hierarchy: everything belongs to
    it through Campus."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    # Short name shown instead of `name` when filled in (see __str__).
    abbreviation = models.CharField(max_length=20)
    # Optional contact details for the church as a whole.
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)
    # Stored under MEDIA_ROOT/church_photos/. Optional (empty string when unset).
    photo = models.ImageField(upload_to="church_photos/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "churches"

    def __str__(self):
        return self.abbreviation or self.name


class Campus(models.Model):
    """A physical site/branch of the church. Ministries, cells and people are
    attached to a campus."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Deleting the church deletes all its campuses (and, cascading, everything below them).
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="campuses")
    name = models.CharField(max_length=150)
    city = models.CharField(max_length=150, blank=True)
    # Optional contact details for this specific campus.
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "campuses"
        constraints = [
            # No two campuses with the same name in the same church.
            models.UniqueConstraint(fields=["church", "name"], name="unique_campus_per_church"),
        ]

    def __str__(self):
        return self.name

    # Shortcuts to list people by role. They run a fresh query on every call
    # (they don't use prefetched data). The same applies to Ministry, Cell and Team.
    def leaders(self):
        return [cl.person for cl in self.leaderships.filter(role=LeadershipRole.LEADER)]

    def co_leaders(self):
        return [cl.person for cl in self.leaderships.filter(role=LeadershipRole.CO_LEADER)]


class CampusLeadership(models.Model):
    """Links a person to a campus as leader or co-leader.

    Same idea as MinistryLeadership/CellLeadership/TeamLeadership: a row here,
    not a fixed field on Person — leadership of a campus must be reassignable
    without touching the person's own record, and a member can become leader
    (or stop being one) later.

    A campus leader can see and edit every person at that campus.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Reverse access: campus.leaderships.all()
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="leaderships")
    # Reverse access: person.campus_leaderships.all()
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="campus_leaderships"
    )
    role = models.CharField(max_length=16, choices=LeadershipRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # A person holds at most one role per campus (can't be both leader
            # and co-leader of the same campus).
            models.UniqueConstraint(fields=["campus", "person"], name="unique_campus_leadership_per_person"),
        ]

    def __str__(self):
        return f"{self.person} — {self.get_role_display()} de {self.campus}"


# =============================================================================
# Ministries
# =============================================================================


class Ministry(models.Model):
    """A ministry at one specific campus (e.g. "Jovens" at the Centro campus).

    The same ministry type (`kind`) exists at several campuses, one record per
    campus. Leadership of a type across every campus lives in
    MinistryGeneralLeadership.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="ministries")
    # Ministry type (Jovens, Crianças, Mídia, Reconciliação, ...). See
    # choices.MinistryKind and PRODUCTION_MINISTRY_KINDS (technical ministries).
    kind = models.CharField(max_length=32, choices=MinistryKind.choices)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    # Optional target age range (e.g. Adolescentes 12–17). Informational only:
    # not validated against members' ages.
    min_age = models.PositiveSmallIntegerField(null=True, blank=True)
    max_age = models.PositiveSmallIntegerField(null=True, blank=True)
    # People who take part/serve in the ministry (no leadership implied).
    # Reverse access: person.ministries.all()
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="ministries"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "ministries"
        ordering = ["name"]
        constraints = [
            # Name is unique within the campus (different campuses may reuse names).
            models.UniqueConstraint(fields=["campus", "name"], name="unique_ministry_name_per_campus"),
        ]

    def __str__(self):
        # Touches self.campus: costs an extra query unless the campus was loaded
        # with select_related.
        return f"{self.name} ({self.campus.name})"

    def leaders(self):
        return [ml.person for ml in self.leaderships.filter(role=LeadershipRole.LEADER)]

    def co_leaders(self):
        return [ml.person for ml in self.leaderships.filter(role=LeadershipRole.CO_LEADER)]


class MinistryLeadership(models.Model):
    """Assigns a person as leader or co-leader of a specific (campus × kind) Ministry.

    A row here, not a fixed field on Person — leadership must be reassignable
    without touching the person's own record.

    A ministry leader can register people and see/edit the members of the
    ministries they lead.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Reverse access: ministry.leaderships.all()
    ministry = models.ForeignKey(Ministry, on_delete=models.CASCADE, related_name="leaderships")
    # Reverse access: person.ministry_leaderships.all()
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ministry_leaderships"
    )
    role = models.CharField(max_length=16, choices=LeadershipRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # A person holds at most one role per ministry.
            models.UniqueConstraint(fields=["ministry", "person"], name="unique_leadership_per_person"),
        ]

    def __str__(self):
        return f"{self.person} — {self.get_role_display()} de {self.ministry}"


class MinistryGeneralLeadership(models.Model):
    """The leader(s) of a MinistryKind across every campus (e.g. líder geral de Jovens).

    Unlike MinistryLeadership, this doesn't point to a specific Ministry but to
    a `kind`. There is at most one record per kind (`unique=True`), and the
    people are in `leaders` (there can be more than one).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=32, choices=MinistryKind.choices, unique=True)
    # Reverse access: person.general_leaderships.all()
    leaders = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="general_leaderships",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Líder geral de {self.get_kind_display()}"


# =============================================================================
# Seasons
# =============================================================================


class SeasonQuerySet(models.QuerySet):
    def current(self, on=None):
        """Seasons running on `on` (default: today). Seasons of the same church
        never overlap, so this yields at most one season per church."""
        on = on or timezone.localdate()
        return self.filter(start_date__lte=on, end_date__gte=on)


class Season(models.Model):
    """A period during which cells run (e.g. "2026.2", from August to December).

    Church-wide: every campus follows the same calendar. There is no "is
    current" flag to keep in sync — the current season is simply the one whose
    date range contains today (see SeasonQuerySet.current).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="seasons")
    name = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SeasonQuerySet.as_manager()

    class Meta:
        ordering = ["-start_date"]  # newest first
        constraints = [
            models.UniqueConstraint(fields=["church", "name"], name="unique_season_name_per_church"),
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")), name="season_end_after_start"
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "A data de fim deve ser depois da data de início."})
        # Overlap check lives here (not in a DB constraint) because SQLite, used
        # in tests, has no exclusion constraints.
        overlapping = Season.objects.filter(
            church_id=self.church_id,
            start_date__lte=self.end_date,
            end_date__gte=self.start_date,
        ).exclude(pk=self.pk)
        if overlapping.exists():
            raise ValidationError(
                f'As datas se sobrepõem à temporada "{overlapping.first().name}".'
            )

    @property
    def is_current(self):
        return self.start_date <= timezone.localdate() <= self.end_date


# =============================================================================
# Cells (GT)
# =============================================================================


class CellGeneralLeadership(models.Model):
    """The people in charge of every célula of a church (responsável geral pelas
    células). One record per church; `leaders` may hold more than one person.

    They define the seasons and can see and manage every cell, including
    creating cells, assigning cell leaders and renewing cells into a new season.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    church = models.OneToOneField(
        Church, on_delete=models.CASCADE, related_name="cell_general_leadership"
    )
    # Reverse access: person.cell_general_leaderships.all()
    leaders = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="cell_general_leaderships"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Responsável geral pelas células de {self.church}"


class CampusCellsLeadership(models.Model):
    """Links a person to a campus as the one in charge of that campus's cells
    (responsável pelas células do campus), as leader or co-leader.

    Assigned by the campus leader (or the manager / general cells leader).
    Within that campus they can see and manage every cell, create cells, assign
    cell leaders and renew cells into a new season. Seasons stay church-wide
    (see CellGeneralLeadership). The person must belong to the campus.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Reverse access: campus.cells_leaderships.all()
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="cells_leaderships")
    # Reverse access: person.campus_cells_leaderships.all()
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="campus_cells_leaderships"
    )
    role = models.CharField(max_length=16, choices=LeadershipRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["campus", "person"], name="unique_campus_cells_leadership_per_person"
            ),
        ]

    def __str__(self):
        return f"{self.person} — {self.get_role_display()} das células de {self.campus}"


class CellQuerySet(models.QuerySet):
    def active(self, on=None):
        """Cells running on `on` (default: today): their season covers that date
        and their own start/end dates (when set) do too. Cells without a season
        (created before seasons existed) count as active."""
        on = on or timezone.localdate()
        return (
            self.filter(
                Q(season__isnull=True) | Q(season__start_date__lte=on, season__end_date__gte=on)
            )
            .exclude(start_date__gt=on)
            .exclude(end_date__lt=on)
        )


class Cell(models.Model):
    """A célula/GT — a small group that meets regularly, tied directly to a
    Campus (not to a Ministry), during one Season.

    Members may come from any campus; leaders must belong to the cell's campus
    (enforced in CellViewSet.leaderships).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="cells")
    # PROTECT: a season with cells can't be deleted, or the history would go with it.
    # Nullable only for cells created before seasons existed; the API requires it.
    season = models.ForeignKey(
        Season, on_delete=models.PROTECT, null=True, blank=True, related_name="cells"
    )
    # The same cell in the previous season (set by the "renew" action). Lets the
    # UI walk a cell's history across seasons.
    previous_cell = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="next_cells"
    )
    name = models.CharField(max_length=150)
    # Optional, for a cell that starts later or ends earlier than its season.
    # When empty, the season's dates apply (see effective_start/effective_end).
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    # Meeting day (see choices.Weekday: 1=Monday … 7=Sunday) and start time.
    # Both optional: a cell may not have a fixed schedule yet.
    meeting_day = models.PositiveSmallIntegerField(choices=Weekday.choices, null=True, blank=True)
    meeting_time = models.TimeField(null=True, blank=True)
    # Target audience, reusing the ministry types (e.g. Jovens, Casais).
    # Optional: a cell may have no specific audience.
    target_audience = models.CharField(max_length=32, choices=MinistryKind.choices, blank=True)
    address = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    # Reverse access: person.cells.all()
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="cells")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CellQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["campus", "meeting_day", "meeting_time"], name="cell_schedule_idx"),
        ]
        constraints = [
            # Name is unique within the campus and season (the renewed cell of
            # the next season keeps the same name).
            models.UniqueConstraint(
                fields=["campus", "season", "name"], name="unique_cell_name_per_campus_season"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.campus.name})"

    @property
    def effective_start(self):
        return self.start_date or (self.season.start_date if self.season_id else None)

    @property
    def effective_end(self):
        return self.end_date or (self.season.end_date if self.season_id else None)

    def leaders(self):
        return [cl.person for cl in self.leaderships.filter(role=LeadershipRole.LEADER)]

    def co_leaders(self):
        return [cl.person for cl in self.leaderships.filter(role=LeadershipRole.CO_LEADER)]


class CellLeadership(models.Model):
    """Links a person to a cell as leader or co-leader.

    Same idea as MinistryLeadership/TeamLeadership: a row here, not a fixed
    field on Person — leadership of a célula must be reassignable without
    touching the person's own record, and a member can become leader (or stop
    being one) later.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Reverse access: cell.leaderships.all()
    cell = models.ForeignKey(Cell, on_delete=models.CASCADE, related_name="leaderships")
    # Reverse access: person.cell_leaderships.all()
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cell_leaderships"
    )
    role = models.CharField(max_length=16, choices=LeadershipRole.choices, default=LeadershipRole.LEADER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # A person holds at most one role per cell.
            models.UniqueConstraint(fields=["cell", "person"], name="unique_cell_leadership_per_person"),
        ]

    def __str__(self):
        return f"{self.person} — {self.get_role_display()} de {self.cell}"


class CellMeeting(models.Model):
    """One meeting of a cell on a given date, with who attended.

    `attendees` are people registered in the system (members or leaders of the
    cell, checked in the serializer); first-time guests who aren't registered
    are only counted in `visitors_count`.

    A record with `held=False` says the meeting didn't happen on that date
    (holiday, cancelled), so the date stops showing as pending in the cell's
    calendar (see meeting_calendar.py). It has no attendance.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cell = models.ForeignKey(Cell, on_delete=models.CASCADE, related_name="meetings")
    date = models.DateField()
    held = models.BooleanField(default=True)
    # Why it didn't happen (required when held=False, empty otherwise).
    cancel_reason = models.CharField(max_length=200, blank=True)
    # Free notes: topic, prayer requests, anything worth remembering.
    notes = models.TextField(blank=True)
    # Reverse access: person.attended_cell_meetings.all()
    attendees = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="attended_cell_meetings"
    )
    visitors_count = models.PositiveSmallIntegerField(default=0)
    # Who registered the meeting. SET_NULL keeps the record if that person is deleted.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]  # newest first
        constraints = [
            # One record per cell per day.
            models.UniqueConstraint(fields=["cell", "date"], name="unique_cell_meeting_per_day"),
        ]

    def __str__(self):
        return f"{self.cell} em {self.date}"


# =============================================================================
# Teams
# =============================================================================


class Team(models.Model):
    """A working group within a Ministry (e.g. a rotation/serving team).

    Team members must be members of the parent ministry. This rule is enforced
    in the view that adds members (apps/churches/views.py), not in the database
    or the model.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ministry = models.ForeignKey(Ministry, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    # Reverse access: person.teams.all()
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="teams")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            # Name is unique within the ministry.
            models.UniqueConstraint(fields=["ministry", "name"], name="unique_team_name_per_ministry"),
        ]

    def __str__(self):
        return f"{self.name} ({self.ministry.name})"

    def leaders(self):
        return [tl.person for tl in self.leaderships.filter(role=LeadershipRole.LEADER)]

    def co_leaders(self):
        return [tl.person for tl in self.leaderships.filter(role=LeadershipRole.CO_LEADER)]


class TeamLeadership(models.Model):
    """Links a person to a team as leader or co-leader.

    Same idea as MinistryLeadership, one level down: reassignable, not a fixed
    attribute of the person.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Reverse access: team.leaderships.all()
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="leaderships")
    # Reverse access: person.team_leaderships.all()
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_leaderships"
    )
    role = models.CharField(max_length=16, choices=LeadershipRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # A person holds at most one role per team.
            models.UniqueConstraint(fields=["team", "person"], name="unique_team_leadership_per_person"),
        ]

    def __str__(self):
        return f"{self.person} — {self.get_role_display()} de {self.team}"


# =============================================================================
# Activities and points
# =============================================================================


class Activity(models.Model):
    """Something a team leader assigns to team members, worth points when completed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="activities")
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    # DAILY: can be completed once per day, every day.
    # EVENTUAL: can be completed only once per person.
    kind = models.CharField(max_length=16, choices=ActivityKind.choices)
    # What the activity is worth now. Changing it doesn't affect past
    # completions (see ActivityCompletion.points_awarded).
    points = models.PositiveIntegerField(default=0)
    # Active/inactive flag. Note: no view filters on this field today (it is
    # read-only in the API and only editable through the admin).
    is_active = models.BooleanField(default=True)
    # Who created the activity. If that person is deleted, the activity stays
    # (SET_NULL). related_name="+" disables the reverse accessor on Person,
    # since it isn't used.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "activities"
        ordering = ["-created_at"]  # newest first

    def __str__(self):
        return f"{self.title} ({self.get_kind_display()})"


class ActivityCompletion(models.Model):
    """One person marking one activity as done for one day.

    - DAILY activities can be completed again on a later date (a new row per date).
    - EVENTUAL activities are only ever completed once — enforced in the view,
      since that depends on the activity's `kind`, a cross-model rule the DB
      constraint below (unique per activity + person + date) can't express alone.

    The sum of `points_awarded` over these rows is the person's personal score
    (see people.serializers.personal_total_points).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="completions")
    # Reverse access: person.activity_completions.all()
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="activity_completions"
    )
    # The day this completion counts for (default: today, in the project's
    # timezone). The function timezone.localdate is passed uncalled so it runs
    # for each new row, not once when the server starts.
    date = models.DateField(default=timezone.localdate)
    # Snapshot of activity.points at completion time, so later changes to the
    # activity's points don't rewrite the person's history.
    points_awarded = models.PositiveIntegerField()
    # Exact moment the completion was recorded (unlike `date`).
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-completed_at"]
        constraints = [
            # At most one completion of the same activity by the same person per day.
            models.UniqueConstraint(
                fields=["activity", "person", "date"], name="unique_completion_per_person_per_day"
            ),
        ]

    def __str__(self):
        return f"{self.person} concluiu {self.activity} em {self.date}"


class TeamPointAward(models.Model):
    """Points earned by the team as a whole (e.g. hit a milestone).

    Not attributed to any individual member, and kept separate from personal
    points earned via ActivityCompletion.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="point_awards")
    points = models.PositiveIntegerField()
    # Why the points were given, shown in the team's history.
    reason = models.CharField(max_length=255)
    # Who gave the points. SET_NULL keeps the record if that person is deleted.
    awarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-awarded_at"]  # newest first

    def __str__(self):
        return f"{self.team}: +{self.points} ({self.reason})"

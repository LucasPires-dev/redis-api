import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from .choices import ActivityKind, LeadershipRole, MinistryKind


class Church(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    abbreviation = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "churches"

    def __str__(self):
        return self.abbreviation or self.name


class Campus(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="campuses")
    name = models.CharField(max_length=150)
    city = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "campuses"
        constraints = [
            models.UniqueConstraint(fields=["church", "name"], name="unique_campus_per_church"),
        ]

    def __str__(self):
        return self.name


class Ministry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="ministries")
    kind = models.CharField(max_length=32, choices=MinistryKind.choices)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    min_age = models.PositiveSmallIntegerField(null=True, blank=True)
    max_age = models.PositiveSmallIntegerField(null=True, blank=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="ministries"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "ministries"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["campus", "name"], name="unique_ministry_name_per_campus"),
        ]

    def __str__(self):
        return f"{self.name} ({self.campus.name})"

    def leaders(self):
        return [ml.person for ml in self.leaderships.filter(role=LeadershipRole.LEADER)]

    def co_leaders(self):
        return [ml.person for ml in self.leaderships.filter(role=LeadershipRole.CO_LEADER)]


class MinistryLeadership(models.Model):
    """Assigns a person as leader or co-leader of a specific (campus × kind) Ministry.
    A row here, not a fixed field on Person — leadership must be reassignable without
    touching the person's own record."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ministry = models.ForeignKey(Ministry, on_delete=models.CASCADE, related_name="leaderships")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ministry_leaderships"
    )
    role = models.CharField(max_length=16, choices=LeadershipRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ministry", "person"], name="unique_leadership_per_person"),
        ]

    def __str__(self):
        return f"{self.person} — {self.get_role_display()} de {self.ministry}"


class MinistryGeneralLeadership(models.Model):
    """The leader(s) of a MinistryKind across every campus (e.g. líder geral de Jovens)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=32, choices=MinistryKind.choices, unique=True)
    leaders = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="general_leaderships",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Líder geral de {self.get_kind_display()}"


class Team(models.Model):
    """A working group within a Ministry (e.g. a rotation/serving team). Its members
    are drawn from the parent ministry's members — see clean() / view-level validation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ministry = models.ForeignKey(Ministry, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="teams")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["ministry", "name"], name="unique_team_name_per_ministry"),
        ]

    def __str__(self):
        return f"{self.name} ({self.ministry.name})"

    def leaders(self):
        return [tl.person for tl in self.leaderships.filter(role=LeadershipRole.LEADER)]

    def co_leaders(self):
        return [tl.person for tl in self.leaderships.filter(role=LeadershipRole.CO_LEADER)]


class TeamLeadership(models.Model):
    """Same idea as MinistryLeadership, one level down: reassignable, not a fixed
    attribute of the person."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="leaderships")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_leaderships"
    )
    role = models.CharField(max_length=16, choices=LeadershipRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["team", "person"], name="unique_team_leadership_per_person"),
        ]

    def __str__(self):
        return f"{self.person} — {self.get_role_display()} de {self.team}"


class Activity(models.Model):
    """Something a team leader assigns to team members, worth points when completed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="activities")
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=16, choices=ActivityKind.choices)
    points = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "activities"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.get_kind_display()})"


class ActivityCompletion(models.Model):
    """One person marking one activity as done for one day. 'Daily' activities can be
    completed again on a later date (a new row per date); 'eventual' activities are
    only ever completed once — enforced in the view, since that's a cross-field rule
    the DB constraint below (unique per activity+person+date) can't express alone."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="completions")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="activity_completions"
    )
    date = models.DateField(default=timezone.localdate)
    points_awarded = models.PositiveIntegerField()  # snapshot of activity.points at completion time
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-completed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["activity", "person", "date"], name="unique_completion_per_person_per_day"
            ),
        ]

    def __str__(self):
        return f"{self.person} concluiu {self.activity} em {self.date}"


class TeamPointAward(models.Model):
    """Points earned by the team as a whole (e.g. hit a milestone) — not attributed
    to any individual member, separate from personal points earned via ActivityCompletion."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="point_awards")
    points = models.PositiveIntegerField()
    reason = models.CharField(max_length=255)
    awarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-awarded_at"]

    def __str__(self):
        return f"{self.team}: +{self.points} ({self.reason})"

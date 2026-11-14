import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower

from .managers import PersonManager


class Person(AbstractBaseUser, PermissionsMixin):
    """A church member and, at the same time, the system's login identity.

    There is no separate "User" model: registering a person already creates
    their access account. That is why this model is the project's
    AUTH_USER_MODEL.

    - AbstractBaseUser provides the password (`password`), `last_login` and the
      auth helpers (`set_password`, `check_password`, ...).
    - PermissionsMixin provides `is_superuser`, `groups` and `user_permissions`,
      used by Django's built-in permission system (admin).

    Always create people through `Person.objects.create_user(...)` (see
    PersonManager), never with `Person(...)` directly, so the email gets
    normalized and the password gets hashed.
    """

    # --- Identity ------------------------------------------------------------

    # UUID instead of a sequential integer: doesn't reveal how many members
    # exist and keeps IDs in API URLs from being guessable.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Used as the login (see USERNAME_FIELD). Unique across the whole system.
    email = models.EmailField(unique=True)

    name = models.CharField(max_length=150)

    # Free text (no format validation), optional.
    phone = models.CharField(max_length=20, blank=True)

    # Digits only, validated (see apps.people.cpf). Asked the first time the
    # person pays online: Asaas needs it to create a charge. Never returned by
    # the API (personal data); MeSerializer only says whether it is filled.
    cpf = models.CharField(max_length=11, blank=True, default="")

    # --- Church membership ---------------------------------------------------

    # The campus the person attends. Optional: someone can be registered before
    # being assigned to a campus. If the campus is deleted, the person stays,
    # just without a campus (SET_NULL).
    # Reverse access: `campus.people.all()`.
    campus = models.ForeignKey(
        "churches.Campus", null=True, blank=True, on_delete=models.SET_NULL, related_name="people"
    )

    # Date the person became part of the church (entered by the church).
    # Not to be confused with `date_joined`, which is when the record was created.
    joined_at = models.DateField(null=True, blank=True)

    # --- Spiritual life ------------------------------------------------------

    baptized = models.BooleanField(default=False)
    # Baptism date, if known. Only meaningful when `baptized=True`
    # (this rule is not enforced by the database).
    baptized_at = models.DateField(null=True, blank=True)

    # --- Profile -------------------------------------------------------------

    # Stored under MEDIA_ROOT/people/photos/.
    photo = models.ImageField(upload_to="people/photos", null=True, blank=True)

    # --- Permissions and account status --------------------------------------

    # Mega Igreja manager — a global permission, independent of any ministry
    # leadership (those live in apps.churches.MinistryLeadership /
    # MinistryGeneralLeadership and can change hands at any time).
    # A manager passes every check in apps/churches/permissions.py and
    # apps/people/permissions.py.
    is_manager = models.BooleanField(default=False)

    # Inactive accounts cannot log in. Prefer deactivating over deleting, to
    # keep the person's history.
    is_active = models.BooleanField(default=True)

    # Grants access to the Django Admin. Independent of `is_manager`: a manager
    # doesn't need admin access and vice versa.
    is_staff = models.BooleanField(default=False)

    # When the record was created in the system (set automatically).
    # Not to be confused with `joined_at`.
    date_joined = models.DateTimeField(auto_now_add=True)

    # --- Auth configuration --------------------------------------------------

    objects = PersonManager()

    # Field used as the "username" at login.
    USERNAME_FIELD = "email"
    # Extra fields prompted by `createsuperuser` (besides email and password).
    REQUIRED_FIELDS = ["name"]

    class Meta:
        ordering = ["name"]
        constraints = [
            # Backstop for code paths that skip save() (bulk_create, update()):
            # "Ana@x" and "ana@x" must never be two accounts.
            models.UniqueConstraint(Lower("email"), name="people_person_email_ci_unique"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Covers every way a person is saved (admin, imports, conversions), not
        # just create_user.
        self.email = PersonManager.normalize_email(self.email)
        super().save(*args, **kwargs)

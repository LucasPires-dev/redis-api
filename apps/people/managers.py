from django.contrib.auth.base_user import BaseUserManager


class PersonManager(BaseUserManager):
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        """Lowercases the whole address, not just the domain like Django does.

        Email is the login, and phones often auto-capitalize the first letter:
        without this, "Ana@" and "ana@" would be two separate accounts and
        "Ana@" couldn't log in to the "ana@" one.
        """
        return (email or "").strip().lower()

    def get_by_natural_key(self, username):
        # Login (ModelBackend -> simplejwt) looks the person up by email.
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})

    def _create_person(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Person must have an email address.")
        email = self.normalize_email(email)
        person = self.model(email=email, **extra_fields)
        person.set_password(password)
        person.save(using=self._db)
        return person

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_manager", False)
        return self._create_person(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_manager", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_person(email, password, **extra_fields)

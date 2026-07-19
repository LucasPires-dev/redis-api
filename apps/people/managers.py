from django.contrib.auth.base_user import BaseUserManager


class PersonManager(BaseUserManager):
    use_in_migrations = True

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

from django.db import migrations, models
from django.db.models.functions import Lower


def lowercase_emails(apps, schema_editor):
    """Lowercases stored emails. Refuses to run if two accounts differ only by
    case: merging them is a human decision (whose history wins?), not a migration's."""
    Person = apps.get_model("people", "Person")
    seen = {}
    clashes = []
    for pk, email in Person.objects.values_list("pk", "email"):
        key = email.strip().lower()
        if key in seen:
            clashes.append(key)
        seen.setdefault(key, pk)
    if clashes:
        raise RuntimeError(
            "Emails that differ only by case must be merged by hand before "
            f"migrating: {sorted(set(clashes))}"
        )
    for person in Person.objects.exclude(email=Lower("email")).only("pk", "email"):
        person.email = person.email.strip().lower()
        person.save(update_fields=["email"])


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0002_person_joined_at_photo"),
    ]

    operations = [
        migrations.RunPython(lowercase_emails, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="person",
            constraint=models.UniqueConstraint(Lower("email"), name="people_person_email_ci_unique"),
        ),
    ]

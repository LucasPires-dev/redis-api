from django.db import migrations, models


def empty_audience_to_todos(apps, schema_editor):
    # The cell form labelled an empty audience "Todos", so that's what those
    # cells meant. Now "todos" is an explicit value and empty means "not set".
    Cell = apps.get_model("churches", "Cell")
    Cell.objects.filter(target_audience="").update(target_audience="todos")


def todos_to_empty_audience(apps, schema_editor):
    Cell = apps.get_model("churches", "Cell")
    Cell.objects.filter(target_audience="todos").update(target_audience="")


class Migration(migrations.Migration):
    dependencies = [
        ("churches", "0014_cellmeeting_held"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cell",
            name="target_audience",
            field=models.CharField(
                blank=True,
                choices=[
                    ("todos", "Todos"),
                    ("jovens", "Jovens"),
                    ("criancas", "Crianças"),
                    ("adolescentes", "Adolescentes"),
                    ("mulheres", "Mulheres"),
                    ("homens", "Homens"),
                    ("casais", "Casais"),
                ],
                max_length=32,
            ),
        ),
        migrations.RunPython(empty_audience_to_todos, todos_to_empty_audience),
    ]

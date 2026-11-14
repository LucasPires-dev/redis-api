from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("churches", "0013_cell_leadership_role_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="cellmeeting",
            name="held",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="cellmeeting",
            name="cancel_reason",
            field=models.CharField(blank=True, max_length=200),
        ),
    ]

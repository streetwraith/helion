# Generated by Django 5.0.4 on 2024-05-16 12:34

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sde', '0007_npccorporation'),
    ]

    operations = [
        migrations.AlterField(
            model_name='npccorporation',
            name='faction_id',
            field=models.IntegerField(default=None),
        ),
    ]

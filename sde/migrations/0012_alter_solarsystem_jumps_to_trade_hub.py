# Generated by Django 5.0.4 on 2024-05-25 05:31

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sde', '0011_solarsystem'),
    ]

    operations = [
        migrations.AlterField(
            model_name='solarsystem',
            name='jumps_to_trade_hub',
            field=models.IntegerField(blank=True, db_index=True, default=None, null=True),
        ),
    ]

# Generated by Django 5.0.4 on 2024-05-16 12:12

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sde', '0006_alter_sdetypeid_volume'),
    ]

    operations = [
        migrations.CreateModel(
            name='NpcCorporation',
            fields=[
                ('corporation_id', models.BigIntegerField(primary_key=True, serialize=False)),
                ('faction_id', models.BigIntegerField()),
                ('name', models.CharField(db_index=True, max_length=256)),
            ],
        ),
    ]

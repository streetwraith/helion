# Generated by Django 5.0.4 on 2024-05-25 06:02

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0018_tradehub_system_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketStructure',
            fields=[
                ('structure_id', models.IntegerField(primary_key=True, serialize=False)),
                ('name', models.CharField(blank=True, max_length=512, null=True)),
                ('system_id', models.IntegerField(db_index=True)),
            ],
        ),
    ]

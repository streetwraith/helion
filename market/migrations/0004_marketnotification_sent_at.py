# Generated by Django 5.1.6 on 2025-03-09 12:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0003_marketnotification_character_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='marketnotification',
            name='sent_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]

# Generated by Django 5.1.6 on 2025-03-09 11:56

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0002_marketnotification_marketorder_character_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='marketnotification',
            name='character_id',
            field=models.BigIntegerField(db_index=True, default=1),
            preserve_default=False,
        ),
    ]

# Generated by Django 5.0.4 on 2024-04-17 12:47

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0004_tradehub'),
    ]

    operations = [
        migrations.AddField(
            model_name='marketregionstatus',
            name='region_name',
            field=models.CharField(default='xxx', max_length=128),
            preserve_default=False,
        ),
    ]

# Generated by Django 5.0.4 on 2024-04-17 14:04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0005_marketregionstatus_region_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('client_id', models.IntegerField()),
                ('date', models.DateTimeField()),
                ('is_buy', models.BooleanField()),
                ('is_personal', models.BooleanField()),
                ('journal_ref_id', models.IntegerField()),
                ('location_id', models.IntegerField()),
                ('quantity', models.IntegerField()),
                ('transaction_id', models.IntegerField()),
                ('type_id', models.IntegerField()),
                ('unit_price', models.FloatField()),
            ],
        ),
    ]

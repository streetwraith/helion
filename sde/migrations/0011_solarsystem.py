# Generated by Django 5.0.4 on 2024-05-25 05:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sde', '0010_alter_sdetypeid_market_group_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='SolarSystem',
            fields=[
                ('system_id', models.BigIntegerField(primary_key=True, serialize=False)),
                ('constellation_id', models.BigIntegerField()),
                ('region_id', models.BigIntegerField(db_index=True)),
                ('name', models.CharField(db_index=True, max_length=256)),
                ('security', models.FloatField()),
                ('security_class', models.CharField(max_length=3)),
                ('jumps_to_trade_hub', models.IntegerField(db_index=True)),
            ],
        ),
    ]

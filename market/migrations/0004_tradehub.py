# Generated by Django 5.0.4 on 2024-04-17 12:12

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0003_tradeitem'),
    ]

    operations = [
        migrations.CreateModel(
            name='TradeHub',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=128)),
                ('station_id', models.IntegerField()),
                ('region_id', models.IntegerField()),
            ],
        ),
    ]

# Generated by Django 5.0.4 on 2024-04-17 14:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0006_markettransaction'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='character',
            name='id',
        ),
        migrations.RemoveField(
            model_name='marketorder',
            name='id',
        ),
        migrations.RemoveField(
            model_name='marketregionstatus',
            name='id',
        ),
        migrations.RemoveField(
            model_name='markettransaction',
            name='id',
        ),
        migrations.RemoveField(
            model_name='tradeitem',
            name='id',
        ),
        migrations.AlterField(
            model_name='character',
            name='character_id',
            field=models.IntegerField(primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='marketorder',
            name='order_id',
            field=models.IntegerField(primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='marketregionstatus',
            name='region_id',
            field=models.IntegerField(primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='markettransaction',
            name='transaction_id',
            field=models.IntegerField(primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='tradeitem',
            name='type_id',
            field=models.IntegerField(primary_key=True, serialize=False),
        ),
    ]

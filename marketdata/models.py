from django.db import models

# Read-only accessors for the marketmanager-owned `market` schema. Every model
# is managed = False: marketmanager creates and rewrites these tables, helion
# only reads them. db_table is schema-qualified ('market"."<table>') so it
# resolves regardless of the connection search_path. The orders and history
# tables are partitioned with composite primary keys; queries always go through
# the parent table. `region_status` columns are a contract with marketmanager:
# additions are safe (explicit column list here), renames/removals break us.


class Order(models.Model):
    pk = models.CompositePrimaryKey("region_id", "order_id")
    region_id = models.BigIntegerField()
    order_id = models.BigIntegerField()
    type_id = models.BigIntegerField()
    location_id = models.BigIntegerField()
    system_id = models.BigIntegerField()
    is_buy_order = models.BooleanField()
    price = models.DecimalField(max_digits=20, decimal_places=2)
    volume_remain = models.BigIntegerField()
    volume_total = models.BigIntegerField()
    min_volume = models.IntegerField()
    duration = models.IntegerField()
    range = models.TextField()
    issued = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'market"."orders'


class History(models.Model):
    # http_last_modified is EVE Ref import bookkeeping and is deliberately
    # not mapped; helion never reads it.
    pk = models.CompositePrimaryKey("region_id", "type_id", "date")
    region_id = models.BigIntegerField()
    type_id = models.BigIntegerField()
    date = models.DateField()
    average = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    highest = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    lowest = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    volume = models.BigIntegerField()
    order_count = models.BigIntegerField()

    class Meta:
        managed = False
        db_table = 'market"."history'


class RegionStatus(models.Model):
    region_id = models.BigIntegerField(primary_key=True)
    region_name = models.TextField()
    # NULL until the first successful refresh; not advanced on failure. It
    # means "last successful refresh", never liveness - the error columns
    # carry failures.
    refreshed_at = models.DateTimeField(null=True)
    order_count = models.BigIntegerField(null=True)
    consecutive_errors = models.IntegerField()
    last_error = models.TextField(null=True)
    last_error_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = 'market"."region_status'

    def __str__(self):
        return str(self.region_id) + " " + self.region_name


class OrdersHub(models.Model):
    # The helion-owned view over market.orders (see sync_market_views): adds
    # the computed is_in_trade_hub_range and restricts rows to the trade hub
    # regions. The db_table is unqualified on purpose - the view lives in the
    # connection role's default schema (helion in dev/prod, public in tests).
    pk = models.CompositePrimaryKey("region_id", "order_id")
    region_id = models.BigIntegerField()
    order_id = models.BigIntegerField()
    type_id = models.BigIntegerField()
    location_id = models.BigIntegerField()
    system_id = models.BigIntegerField()
    is_buy_order = models.BooleanField()
    price = models.DecimalField(max_digits=20, decimal_places=2)
    volume_remain = models.BigIntegerField()
    volume_total = models.BigIntegerField()
    min_volume = models.IntegerField()
    duration = models.IntegerField()
    range = models.TextField()
    issued = models.DateTimeField()
    is_in_trade_hub_range = models.BooleanField()

    class Meta:
        managed = False
        db_table = "orders_hub"

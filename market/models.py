from django.db import models

class MarketRegionStatus(models.Model):
    region_id = models.BigIntegerField(primary_key=True)
    region_name = models.CharField(max_length=128)
    orders = models.BigIntegerField()
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return 'region_id: ' + str(self.region_id) + ', region_name: '+self.region_name+', orders: ' + str(self.orders) + ', updated_at: '+str(self.updated_at)

class MarketOrder(models.Model):
    order_id = models.BigIntegerField(primary_key=True)
    duration = models.IntegerField()
    is_buy_order = models.BooleanField(default=False)
    issued = models.DateTimeField()
    location_id = models.BigIntegerField(db_index=True)
    min_volume = models.IntegerField()
    # ISK is stored as numeric(20,2) everywhere: ESI sends at most two
    # decimals, and float sums accumulate drift.
    price = models.DecimalField(max_digits=20, decimal_places=2, db_index=True)
    range = models.CharField(max_length=128)
    system_id = models.BigIntegerField()
    type_id = models.BigIntegerField(db_index=True)
    # Order volumes can exceed 32 bits (Tritanium-class items).
    volume_remain = models.BigIntegerField()
    volume_total = models.BigIntegerField()
    region_id = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_in_trade_hub_range = models.BooleanField(default=True)
    character_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    def __str__(self):
        return str(self.order_id) + ' ' + str(self.type_id)
    
class MarketTransaction(models.Model):
    transaction_id = models.BigIntegerField(primary_key=True)
    character_id = models.BigIntegerField(db_index=True)
    client_id = models.BigIntegerField()
    date = models.DateTimeField()
    is_buy = models.BooleanField()
    is_personal = models.BooleanField()
    journal_ref_id = models.BigIntegerField()
    location_id = models.BigIntegerField()
    quantity = models.IntegerField()
    type_id = models.BigIntegerField()
    unit_price = models.DecimalField(max_digits=20, decimal_places=2)
    class Meta:
        indexes = [
            models.Index(fields=['is_buy', 'location_id', 'type_id']),
        ]
    def __str__(self):
        return str(self.date) + ' ' + ('buy' if self.is_buy else 'sell') + ' ' + str(self.quantity) + 'x ' + str(self.type_id) + ' for ' + str(self.unit_price) + '/ea' + ' in ' + str(self.location_id)
    
class MarketHistory(models.Model):
    type_id = models.BigIntegerField()
    region_id = models.BigIntegerField()
    date = models.DateField(db_index=True)
    average = models.DecimalField(max_digits=20, decimal_places=2)
    highest = models.DecimalField(max_digits=20, decimal_places=2)
    lowest = models.DecimalField(max_digits=20, decimal_places=2)
    order_count = models.BigIntegerField()
    volume = models.BigIntegerField()
    class Meta:
        indexes = [
            models.Index(fields=['type_id', 'region_id']),
        ]
        constraints = [
            # The delete+insert sync maintained this only in practice; the
            # Sum-based bulk averages assume it.
            models.UniqueConstraint(fields=['region_id', 'type_id', 'date'],
                                    name='uq_markethistory_region_type_date'),
        ]
    def __str__(self):
        return str(self.type_id) + ' ' + str(self.date)
    
class TradeHub(models.Model):
    name = models.CharField(max_length=128)
    station_id = models.BigIntegerField()
    region_id = models.BigIntegerField()
    system_id = models.BigIntegerField(default=None, blank=True, null=True)
    def __str__(self):
        return self.name
    
class TradeItem(models.Model):
    type_id = models.BigIntegerField(primary_key=True)
    name = models.CharField(max_length=512, blank=True, null=True)
    group_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    market_group_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    def __str__(self):
        return str(self.type_id) + ' ' + str(self.name)

class WalletJournal(models.Model):
    journal_id = models.BigIntegerField(primary_key=True)
    character_id = models.BigIntegerField(db_index=True)
    # Four decimals: transaction_tax journal amounts really carry them
    # (verified against prod data), so numeric(20,4) loses nothing.
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    balance = models.DecimalField(max_digits=20, decimal_places=4)
    date = models.DateTimeField()
    description = models.CharField(max_length=512, blank=True, null=True)
    first_party_id = models.BigIntegerField(blank=True, null=True)
    second_party_id = models.BigIntegerField(blank=True, null=True)
    reason = models.CharField(max_length=512, blank=True, null=True)
    ref_type = models.CharField(db_index=True, max_length=128)
    context_id = models.BigIntegerField(blank=True, null=True)
    context_id_type = models.CharField(max_length=128, blank=True, null=True)
    tax = models.DecimalField(max_digits=20, decimal_places=4, blank=True, null=True)
    tax_receiver_id = models.BigIntegerField(blank=True, null=True)

class MarketNotification(models.Model):
    order_id = models.BigIntegerField(db_index=True)
    character_id = models.BigIntegerField(db_index=True)
    event_type = models.CharField(max_length=32)
    notification_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)

class MarketOrderUndercut(models.Model):
    type_id = models.BigIntegerField(db_index=True)
    region_id = models.BigIntegerField(db_index=True)
    character_id = models.BigIntegerField(db_index=True)
    order_id = models.BigIntegerField()
    order_price = models.DecimalField(max_digits=20, decimal_places=2)
    order_issued = models.DateTimeField()
    competitor_order_id = models.BigIntegerField()
    competitor_price = models.DecimalField(max_digits=20, decimal_places=2)
    competitor_issued = models.DateTimeField()
    is_buy_order = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['order_id', 'order_issued'], name='uc_order_modification')
        ]

class TrackedCharacter(models.Model):
    # What to fetch for this character, as comma-separated tags. 'orders' is
    # the only tag today; transactions, contracts etc. may join later.
    character_name = models.CharField(max_length=128, unique=True)
    tracks = models.CharField(max_length=128, default='orders')

    def track_list(self):
        return [tag.strip() for tag in self.tracks.split(',') if tag.strip()]

    def __str__(self):
        return self.character_name + ' (' + self.tracks + ')'

class CharacterOrder(models.Model):
    # Which live market orders are ours, joined onto market.orders at read
    # time. Marketmanager holds no authed token, so ownership stays helion's.
    order_id = models.BigIntegerField(primary_key=True)
    character_id = models.BigIntegerField(db_index=True)

class CharacterAsset(models.Model):
    # The assets route payload as ESI sends it, rewritten wholesale per
    # character by the assets feed. Views read only station rows today; the
    # rest is stored for future use.
    item_id = models.BigIntegerField(primary_key=True)
    character_id = models.BigIntegerField(db_index=True)
    type_id = models.BigIntegerField()
    quantity = models.BigIntegerField()
    location_id = models.BigIntegerField()
    location_type = models.CharField(max_length=32)
    location_flag = models.CharField(max_length=64)
    is_singleton = models.BooleanField()
    is_blueprint_copy = models.BooleanField(null=True, blank=True)

class EsiFetchState(models.Model):
    # One row per (character, feed): the ESI fetch scheduler's pacing and
    # error state. Clearing next_due forces a fetch on the next watchdog
    # tick; the re-enable admin action clears disabled_at and the counters.
    character_name = models.CharField(max_length=128)
    feed = models.CharField(max_length=16)
    next_due = models.DateTimeField(null=True, blank=True)
    last_success = models.DateTimeField(null=True, blank=True)
    consecutive_errors = models.IntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    disabled_reason = models.TextField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['character_name', 'feed'], name='uc_fetch_state')
        ]

    def __str__(self):
        return self.character_name + ' ' + self.feed

class SystemHubJumps(models.Model):
    # Jumps from a solar system to its region's trade hub. Non-CCP, ESI-derived;
    # rebuilt by `manage.py recompute_hub_jumps`. Lives here rather than on the sde
    # solar-system table because the sde schema is read-only, owned by sdemanager.
    system_id = models.BigIntegerField(primary_key=True)
    jumps_to_trade_hub = models.IntegerField()

    def __str__(self):
        return str(self.system_id) + ': ' + str(self.jumps_to_trade_hub) + ' jumps'
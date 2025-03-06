from django.db import models
import json

class MarketRegionStatus(models.Model):
    region_id = models.IntegerField(primary_key=True)
    region_name = models.CharField(max_length=128)
    orders = models.IntegerField()
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return 'region_id: ' + str(self.region_id) + ', region_name: '+self.region_name+', orders: ' + str(self.orders) + ', updated_at: '+str(self.updated_at)

class MarketOrder(models.Model):
    order_id = models.IntegerField(primary_key=True)
    duration = models.IntegerField()
    is_buy_order = models.BooleanField(default=False)
    issued = models.DateTimeField()
    location_id = models.IntegerField(db_index=True)
    min_volume = models.IntegerField()
    price = models.FloatField(db_index=True)
    range = models.CharField(max_length=128)
    system_id = models.IntegerField()
    type_id = models.IntegerField(db_index=True)
    volume_remain = models.IntegerField()
    volume_total = models.IntegerField()
    region_id = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_in_trade_hub_range = models.BooleanField(default=True)
    def __str__(self):
        return str(self.order_id) + ' ' + str(self.type_id)
    
class MarketTransaction(models.Model):
    transaction_id = models.IntegerField(primary_key=True)
    client_id = models.IntegerField()
    date = models.DateTimeField()
    is_buy = models.BooleanField()
    is_personal = models.BooleanField()
    journal_ref_id = models.IntegerField()
    location_id = models.IntegerField()
    quantity = models.IntegerField()
    type_id = models.IntegerField()
    unit_price = models.FloatField()
    class Meta:
        indexes = [
            models.Index(fields=['is_buy', 'location_id', 'type_id']),
        ]
    def __str__(self):
        return str(self.date) + ' ' + ('buy' if self.is_buy else 'sell') + ' ' + str(self.quantity) + 'x ' + str(self.type_id) + ' for ' + str(self.unit_price) + '/ea' + ' in ' + str(self.location_id)
    
class MarketHistory(models.Model):
    type_id = models.IntegerField()
    region_id = models.IntegerField()
    date = models.DateField(db_index=True)
    average = models.FloatField()
    highest = models.FloatField()
    lowest = models.FloatField()
    order_count = models.IntegerField()
    volume = models.IntegerField()
    class Meta:
        indexes = [
            models.Index(fields=['type_id', 'region_id']),
        ]
    def __str__(self):
        return str(self.type_id) + ' ' + str(self.date)
    
# class MarketHistoryEncoder(json.JSONEncoder):
#     def default(self, obj):
#         if isinstance(obj, MarketHistory):
#             return {
#                 'type_id': obj.type_id,
#                 'region_id': obj.region_id,
#                 'date': obj.date.strftime('%Y-%m-%d'),
#                 'average': obj.average,
#                 'highest': obj.highest,
#                 'lowest': obj.lowest,
#                 'order_count': obj.order_count,
#                 'volume': obj.volume,
#             }
#         return json.JSONEncoder.default(self, obj)

class TradeHub(models.Model):
    name = models.CharField(max_length=128)
    station_id = models.IntegerField()
    region_id = models.IntegerField()
    system_id = models.IntegerField(default=None, blank=True, null=True)
    def __str__(self):
        return self.name

class AuthData(models.Model):
    access_token = models.CharField(max_length=2048)
    expires_in = models.FloatField(default=0.0)
    token_type = models.CharField(max_length=128)
    refresh_token = models.CharField(max_length=2048)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return self.access_token
    
class Character(models.Model):
    character_id = models.IntegerField(primary_key=True)
    character_name = models.CharField(max_length=128)
    def __str__(self):
        return str(self.character_id) + ' ' + self.character_name
    
class TradeItem(models.Model):
    type_id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=512, blank=True, null=True)
    group_id = models.IntegerField(db_index=True, blank=True, null=True)
    market_group_id = models.IntegerField(db_index=True, blank=True, null=True)
    def __str__(self):
        return str(self.type_id) + ' ' + str(self.name)
    
class MarketStructure(models.Model):
    structure_id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=512, blank=True, null=True)
    system_id = models.IntegerField(db_index=True)

class WalletJournal(models.Model):
    journal_id = models.IntegerField(primary_key=True)
    amount = models.FloatField()
    balance = models.FloatField()
    date = models.DateTimeField()
    description = models.CharField(max_length=512, blank=True, null=True)
    first_party_id = models.IntegerField(blank=True, null=True)
    second_party_id = models.IntegerField(blank=True, null=True)
    reason = models.CharField(max_length=512, blank=True, null=True)
    ref_type = models.CharField(db_index=True, max_length=128)
    context_id = models.IntegerField(blank=True, null=True)
    context_id_type = models.CharField(max_length=128, blank=True, null=True)
    tax = models.FloatField(blank=True, null=True)
    tax_receiver_id = models.IntegerField(blank=True, null=True)
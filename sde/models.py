from django.db import models

class SdeTypeId(models.Model):
    type_id = models.BigIntegerField(primary_key=True)
    group_id = models.BigIntegerField(db_index=True)
    meta_id = models.IntegerField(db_index=True, default=None, blank=True, null=True)
    market_group_id = models.BigIntegerField(db_index=True, default=None, blank=True, null=True)
    name = models.CharField(max_length=512)
    volume = models.FloatField(default=None, blank=True, null=True)
    def __str__(self):
        return str(self.type_id) + ' ' + self.name
    
class MarketGroup(models.Model):
    market_group_id = models.BigIntegerField(primary_key=True)
    has_types = models.BooleanField()
    name = models.CharField(max_length=512)
    description = models.CharField(max_length=1024)
    parent_group_id = models.BigIntegerField(db_index=True, default=None, blank=True, null=True)
    def __str__(self):
        return str(self.market_group_id) + ' ' + self.name + ' ' + self.description

class NpcCorporation(models.Model):
    corporation_id = models.BigIntegerField(primary_key=True)
    faction_id = models.BigIntegerField(default=None, blank=True, null=True)
    name = models.CharField(max_length=256, db_index=True)
    def __str__(self):
        return str(self.corporation_id) + ' ' + self.name
    
class SolarSystem(models.Model):
    system_id = models.BigIntegerField(primary_key=True)
    constellation_id = models.BigIntegerField()
    region_id = models.BigIntegerField(db_index=True)
    name = models.CharField(max_length=256, db_index=True)
    security = models.FloatField()
    security_class = models.CharField(max_length=3)
    jumps_to_trade_hub = models.IntegerField(db_index=True, default=None, blank=True, null=True)
    def __str__(self):
        fields = vars(self)
        field_strings = [f"{key}={value!r}" for key, value in fields.items()]
        return f"{', '.join(field_strings)}"
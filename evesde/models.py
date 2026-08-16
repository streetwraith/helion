from django.db import models

# Read-only accessors for the sdemanager-owned `sde` schema. Every model is
# managed = False: sdemanager creates and rewrites these tables, helion only reads
# them. db_table is schema-qualified ('sde"."<table>') so it resolves regardless of
# the connection search_path. Field names keep helion's historical spelling and map
# to the sde columns via db_column (e.g. name -> name_en).


class Type(models.Model):
    type_id = models.BigIntegerField(primary_key=True, db_column="_key")
    name = models.CharField(max_length=512, db_column="name_en")
    group_id = models.BigIntegerField()
    market_group_id = models.BigIntegerField(null=True)
    meta_group_id = models.IntegerField(null=True)
    volume = models.FloatField(null=True)
    portion_size = models.IntegerField(null=True)

    class Meta:
        managed = False
        db_table = 'sde"."types'

    def __str__(self):
        return str(self.type_id) + " " + self.name


class Group(models.Model):
    """Inventory groups, the tree that groups a skill under Gunnery or Trade.
    Not the market groups below: a type carries both, and they differ."""
    group_id = models.BigIntegerField(primary_key=True, db_column="_key")
    name = models.CharField(max_length=512, db_column="name_en")
    category_id = models.BigIntegerField(null=True)

    class Meta:
        managed = False
        db_table = 'sde"."groups'

    def __str__(self):
        return str(self.group_id) + " " + self.name


class TypeDogmaAttribute(models.Model):
    """One dogma attribute value of one type, for example the implant slot.

    sdemanager flattens a record array into a `<parent>__<path>` child table, so
    the type id arrives as `_parent_key` and the row's position in the array as
    `_ordinal_1`. Those two are the key.
    """
    pk = models.CompositePrimaryKey("type_id", "ordinal")
    type_id = models.BigIntegerField(db_column="_parent_key")
    ordinal = models.IntegerField(db_column="_ordinal_1")
    attribute_id = models.BigIntegerField()
    value = models.FloatField(null=True)

    class Meta:
        managed = False
        db_table = 'sde"."type_dogma__dogma_attributes'


class MarketGroup(models.Model):
    market_group_id = models.BigIntegerField(primary_key=True, db_column="_key")
    parent_group_id = models.BigIntegerField(null=True)
    name = models.CharField(max_length=512, db_column="name_en")
    has_types = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'sde"."market_groups'

    def __str__(self):
        return str(self.market_group_id) + " " + self.name


class MetaGroup(models.Model):
    meta_group_id = models.BigIntegerField(primary_key=True, db_column="_key")
    name = models.CharField(max_length=256, db_column="name_en")

    class Meta:
        managed = False
        db_table = 'sde"."meta_groups'

    def __str__(self):
        return str(self.meta_group_id) + " " + self.name


class NpcCorporation(models.Model):
    corporation_id = models.BigIntegerField(primary_key=True, db_column="_key")
    faction_id = models.BigIntegerField(null=True)
    name = models.CharField(max_length=256, db_column="name_en")

    class Meta:
        managed = False
        db_table = 'sde"."npc_corporations'

    def __str__(self):
        return str(self.corporation_id) + " " + self.name


class MapSolarSystem(models.Model):
    system_id = models.BigIntegerField(primary_key=True, db_column="_key")
    region_id = models.BigIntegerField()
    name = models.CharField(max_length=256, db_column="name_en")
    security_status = models.FloatField()

    class Meta:
        managed = False
        db_table = 'sde"."map_solar_systems'

    def __str__(self):
        return str(self.system_id) + " " + self.name


class NpcStationName(models.Model):
    # A derived view, not an exported entity: the SDE carries no station name,
    # so sdemanager composes it from six entities (see its PROJECT.md) and
    # publishes the result here. Never recompose it - the moon rule is subtle.
    station_id = models.BigIntegerField(primary_key=True)
    name = models.TextField()

    class Meta:
        managed = False
        db_table = 'sde"."npc_station_names'

    def __str__(self):
        return str(self.station_id) + " " + self.name

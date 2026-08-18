from django.db import models

class MarketTransaction(models.Model):
    transaction_id = models.BigIntegerField(primary_key=True)
    # Null on a row only the corporation wallet route reported.
    character_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    # Set on a row the corporation feeds own. A row can carry both: the
    # character route names who executed the trade, the corporation route
    # names the wallet that paid, and neither write clears the other.
    corporation_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    # Which of the seven corporation wallets. Null on a character row.
    division = models.IntegerField(blank=True, null=True)
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
    character_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    # Set on a row the corporation feeds own. A row can carry both: the
    # character route names who executed the trade, the corporation route
    # names the wallet that paid, and neither write clears the other.
    corporation_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    # Which of the seven corporation wallets. Null on a character row.
    division = models.IntegerField(blank=True, null=True)
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

class MarketOrderUndercut(models.Model):
    type_id = models.BigIntegerField(db_index=True)
    region_id = models.BigIntegerField(db_index=True)
    character_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    # Exactly one of the two owner columns is set: the undercut job runs per
    # owner, so a row names the character or the corporation, never both.
    corporation_id = models.BigIntegerField(db_index=True, blank=True, null=True)
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
    # What to fetch for this character, as comma-separated tags. Valid tags
    # are the esi_scheduler FEEDS keys: orders, wallet, assets, contracts.
    # Unknown tags are silently ignored.
    character_name = models.CharField(max_length=128, unique=True)
    tracks = models.CharField(max_length=128, default='orders')
    # Whether the profit statistics count this character's wallet. A character
    # can be worth fetching without being a trader - an alt that only hauls or
    # runs missions would otherwise pull its mission rewards into the numbers.
    is_trader = models.BooleanField(default=True)

    def track_list(self):
        return [tag.strip() for tag in self.tracks.split(',') if tag.strip()]

    def __str__(self):
        return self.character_name + ' (' + self.tracks + ')'

class CharacterOrder(models.Model):
    # Which live market orders are ours, joined onto market.orders at read
    # time. Marketmanager holds no authed token, so ownership stays helion's.
    order_id = models.BigIntegerField(primary_key=True)
    character_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    # Set on a row the corporation feeds own. A row can carry both: the
    # character route names who executed the trade, the corporation route
    # names the wallet that paid, and neither write clears the other.
    corporation_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    # The character orders route reports whether the order was placed on
    # behalf of the corporation. Null on a row only the corporation route
    # reported, where every order is the corporation's by definition.
    is_corporation = models.BooleanField(blank=True, null=True)

class CharacterAsset(models.Model):
    # The assets route payload as ESI sends it, rewritten wholesale per
    # character by the assets feed. Views read only station rows today; the
    # rest is stored for future use.
    item_id = models.BigIntegerField(primary_key=True)
    character_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    # Exactly one of the two owner columns is set: an item sits in a character
    # hangar or in a corporation hangar, never in both, which is why each assets
    # feed can delete its own rows wholesale.
    corporation_id = models.BigIntegerField(db_index=True, blank=True, null=True)
    type_id = models.BigIntegerField()
    quantity = models.BigIntegerField()
    location_id = models.BigIntegerField()
    location_type = models.CharField(max_length=32)
    location_flag = models.CharField(max_length=64)
    is_singleton = models.BooleanField()
    is_blueprint_copy = models.BooleanField(null=True, blank=True)
    # The name the owner gave a ship or a container, from a second route. Null
    # means the item carries no name of its own, which is most of them.
    name = models.CharField(max_length=256, null=True, blank=True)

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

class CharacterContract(models.Model):
    # The contracts route payload as ESI sends it. contract_id is the primary
    # key rather than (character, contract): a contract is one global object,
    # so two of our characters party to the same one upsert into one row. The
    # party columns below are what a per-character filter reads instead.
    # Nothing derived is stored - expiry, the delivery deadline and the names
    # are computed at read time, so fixing a rule never needs a backfill.
    contract_id = models.BigIntegerField(primary_key=True)
    acceptor_id = models.BigIntegerField()
    assignee_id = models.BigIntegerField()
    issuer_id = models.BigIntegerField()
    issuer_corporation_id = models.BigIntegerField()
    availability = models.CharField(max_length=32)
    status = models.CharField(max_length=32)
    type = models.CharField(max_length=32)
    title = models.CharField(max_length=512, blank=True, null=True)
    for_corporation = models.BooleanField()
    start_location_id = models.BigIntegerField(blank=True, null=True)
    end_location_id = models.BigIntegerField(blank=True, null=True)
    buyout = models.DecimalField(max_digits=20, decimal_places=2, blank=True, null=True)
    collateral = models.DecimalField(max_digits=20, decimal_places=2, blank=True, null=True)
    price = models.DecimalField(max_digits=20, decimal_places=2, blank=True, null=True)
    reward = models.DecimalField(max_digits=20, decimal_places=2, blank=True, null=True)
    volume = models.FloatField(blank=True, null=True)
    days_to_complete = models.IntegerField(blank=True, null=True)
    date_issued = models.DateTimeField()
    date_expired = models.DateTimeField()
    date_accepted = models.DateTimeField(blank=True, null=True)
    date_completed = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return str(self.contract_id) + ' ' + self.type + ' (' + self.status + ')'

class EveName(models.Model):
    # Names for the ids a contract carries. Two routes fill it: /universe/names
    # for characters and corporations, /universe/structures/{id} for player
    # structures. NPC stations never enter it - sde.npc_station_names answers
    # those. A null name means ESI refused the id (no docking access on a
    # structure) and is never retried: the refusal is permanent in practice,
    # and a retry timer would spend the error budget shared with marketmanager.
    entity_id = models.BigIntegerField(primary_key=True)
    name = models.CharField(max_length=256, blank=True, null=True)
    category = models.CharField(max_length=32)
    resolved_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.entity_id) + ': ' + (self.name or '<unresolved>')

class SystemHubJumps(models.Model):
    # Jumps from a solar system to its region's trade hub. Non-CCP, ESI-derived;
    # rebuilt by `manage.py recompute_hub_jumps`. Lives here rather than on the sde
    # solar-system table because the sde schema is read-only, owned by sdemanager.
    system_id = models.BigIntegerField(primary_key=True)
    jumps_to_trade_hub = models.IntegerField()

    def __str__(self):
        return str(self.system_id) + ': ' + str(self.jumps_to_trade_hub) + ' jumps'
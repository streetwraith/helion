"""ESI synchronization: transactions, journal, character orders, assets."""
import logging

from django.db import transaction

from esi.exceptions import HTTPNotModified
from esi.models import Token
from helion.providers import esi
from market.models import CharacterOrder, MarketTransaction, TrackedCharacter, WalletJournal

logger = logging.getLogger(__name__)

def update_market_transactions(character_id):
    token = Token.get_token(character_id, 'esi-wallet.read_character_wallet.v1')
    try:
        api_market_transactions = esi.client.Wallet.GetCharactersCharacterIdWalletTransactions(
            character_id=character_id, token=token).results()
    except HTTPNotModified:
        logger.info("wallet transactions unchanged for character %s, skipping", character_id)
        return
    market_transactions = []
    for value in api_market_transactions:
        market_transaction = MarketTransaction(**value.model_dump())
        market_transaction.character_id = character_id
        market_transactions.append(market_transaction)
    MarketTransaction.objects.bulk_create(market_transactions,
        update_conflicts=True,
        unique_fields=['transaction_id'],
        update_fields=['client_id', 'character_id', 'date', 'is_buy', 'journal_ref_id', 'location_id', 'quantity', 'type_id', 'unit_price'])

# Journal fields ESI omits from an entry when they carry no value.
OPTIONAL_JOURNAL_FIELDS = (
    'description', 'first_party_id', 'second_party_id', 'reason',
    'context_id', 'context_id_type', 'tax', 'tax_receiver_id',
)

def get_wallet_journal(character_id):
    token = Token.get_token(character_id, 'esi-wallet.read_character_wallet.v1')
    try:
        journal_data = esi.client.Wallet.GetCharactersCharacterIdWalletJournal(
            character_id=character_id, token=token).results()
    except HTTPNotModified:
        logger.info("wallet journal unchanged for character %s, skipping", character_id)
        return

    journal_entries = [
        WalletJournal(
            character_id=character_id,
            journal_id=value['id'],
            amount=value['amount'],
            balance=value['balance'],
            date=value['date'],
            ref_type=value['ref_type'],
            **{field: value[field] for field in OPTIONAL_JOURNAL_FIELDS if field in value},
        )
        for value in (entry.model_dump() for entry in journal_data)
    ]

    WalletJournal.objects.bulk_create(journal_entries,
        update_conflicts=True,
        unique_fields=['journal_id'],
        update_fields=['amount', 'balance', 'date', 'description', 'first_party_id', 'second_party_id', 'reason', 'ref_type', 'context_id', 'context_id_type', 'tax', 'tax_receiver_id']
    )

def refresh_character_orders():
    """Rewrite CharacterOrder from ESI for every character tracking orders.

    The route is server-cached 1200 s and unpaginated at our volume, so a
    20-minute cadence wastes no calls and touches no rate-limit bucket."""
    rows = []
    for tracked in TrackedCharacter.objects.all():
        if 'orders' not in tracked.track_list():
            continue
        character_id = Token.objects.get(character_name=tracked.character_name).character_id
        # use_etag=False: a 304 here carries no body, and this call always
        # needs the full order list.
        orders = esi.client.Market.GetCharactersCharacterIdOrders(
            character_id=character_id,
            token=Token.get_token(character_id, 'esi-markets.read_character_orders.v1')
        ).results(use_etag=False)
        rows.extend(
            CharacterOrder(order_id=order.order_id, character_id=character_id)
            for order in orders
        )

    # Atomic so a failure between the two statements cannot leave the
    # ownership table empty.
    with transaction.atomic():
        CharacterOrder.objects.all().delete()
        CharacterOrder.objects.bulk_create(rows)
    logger.info("character orders refreshed: %s rows", len(rows))


def get_character_assets(character_id, location_ids, trade_items):
    token = Token.get_token(character_id, 'esi-assets.read_assets.v1')
    # use_etag=False: this runs in the request path and always needs the body.
    api_character_assets = esi.client.Assets.GetCharactersCharacterIdAssets(
        character_id=character_id, token=token).results(use_etag=False)

    return_by_location = isinstance(location_ids, list)

    if not return_by_location:
        location_ids = [location_ids]

    if return_by_location:
        # Return format: {location_id: {type_id: quantity}}
        character_assets = {}
        for value in api_character_assets:
            type_id = value.type_id
            location_id = value.location_id
            if type_id in trade_items and value.location_type == 'station' and location_id in location_ids:
                if location_id not in character_assets:
                    character_assets[location_id] = {}
                if type_id not in character_assets[location_id]:
                    character_assets[location_id][type_id] = 0
                character_assets[location_id][type_id] = value.quantity + character_assets[location_id][type_id]
    else:
        # Return format: {type_id: quantity} (aggregated totals)
        character_assets = {}
        for value in api_character_assets:
            type_id = value.type_id
            if type_id in trade_items and value.location_type == 'station' and value.location_id in location_ids:
                if type_id not in character_assets:
                    character_assets[type_id] = 0
                character_assets[type_id] = value.quantity + character_assets[type_id]

    return character_assets

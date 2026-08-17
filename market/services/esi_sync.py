"""ESI synchronization: the per-character feed fetches (orders, wallet, assets).

Every fetch function takes a character_id and returns the response's Expires
header as an aware datetime (or None), so the scheduler can pace the next
fetch off the server cache instead of a fixed interval. A 304 counts as a
successful fetch that changes nothing.
"""
import logging
from email.utils import parsedate_to_datetime

from django.db import transaction

from esi.exceptions import HTTPNotModified
from esi.models import Token
from helion.providers import esi
from market.models import (
    CharacterAsset, CharacterContract, CharacterOrder, MarketTransaction, WalletJournal)
from market.services import names

logger = logging.getLogger(__name__)


def _expires(headers):
    value = headers.get('Expires') if headers else None
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    # A header without a timezone parses naive and would blow up aware-datetime
    # comparisons later; treat it as absent (the scheduler falls back to TTL).
    return parsed if parsed.tzinfo is not None else None


def update_market_transactions(character_id):
    token = Token.get_token(character_id, 'esi-wallet.read_character_wallet.v1')
    try:
        api_market_transactions, response = esi.client.Wallet.GetCharactersCharacterIdWalletTransactions(
            character_id=character_id, token=token).results(return_response=True)
    except HTTPNotModified as not_modified:
        logger.info("wallet transactions unchanged for character %s, skipping", character_id)
        return _expires(not_modified.headers)
    market_transactions = []
    for value in api_market_transactions:
        market_transaction = MarketTransaction(**value.model_dump())
        market_transaction.character_id = character_id
        market_transactions.append(market_transaction)
    MarketTransaction.objects.bulk_create(market_transactions,
        update_conflicts=True,
        unique_fields=['transaction_id'],
        update_fields=['client_id', 'character_id', 'date', 'is_buy', 'journal_ref_id', 'location_id', 'quantity', 'type_id', 'unit_price'])
    return _expires(response.headers)

# Journal fields ESI omits from an entry when they carry no value.
OPTIONAL_JOURNAL_FIELDS = (
    'description', 'first_party_id', 'second_party_id', 'reason',
    'context_id', 'context_id_type', 'tax', 'tax_receiver_id',
)

def get_wallet_journal(character_id):
    token = Token.get_token(character_id, 'esi-wallet.read_character_wallet.v1')
    try:
        journal_data, response = esi.client.Wallet.GetCharactersCharacterIdWalletJournal(
            character_id=character_id, token=token).results(return_response=True)
    except HTTPNotModified as not_modified:
        logger.info("wallet journal unchanged for character %s, skipping", character_id)
        return _expires(not_modified.headers)

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
    return _expires(response.headers)


def refresh_character_wallet(character_id):
    """Both wallet routes together: they serve one consumer (the profit
    statistics) and share one rate bucket. The later Expires wins so the
    pair repolls as one."""
    expirations = [update_market_transactions(character_id), get_wallet_journal(character_id)]
    expirations = [value for value in expirations if value is not None]
    return max(expirations) if expirations else None


def refresh_character_orders(character_id):
    """Rewrite one character's CharacterOrder rows from ESI.

    The rewrite is per character, so a 304 (order list unchanged) is a
    correct no-op instead of a lost character."""
    try:
        orders, response = esi.client.Market.GetCharactersCharacterIdOrders(
            character_id=character_id,
            token=Token.get_token(character_id, 'esi-markets.read_character_orders.v1')
        ).results(return_response=True)
    except HTTPNotModified as not_modified:
        logger.info("orders unchanged for character %s, skipping", character_id)
        return _expires(not_modified.headers)

    rows = [
        CharacterOrder(order_id=order.order_id, character_id=character_id)
        for order in orders
    ]
    # Atomic so a failure between the two statements cannot leave the
    # character without ownership rows.
    with transaction.atomic():
        CharacterOrder.objects.filter(character_id=character_id).delete()
        CharacterOrder.objects.bulk_create(rows)
    logger.info("character %s orders refreshed: %s rows", character_id, len(rows))
    return _expires(response.headers)


# The payload keys match the model field names one for one, so a field ESI
# adds later is dropped here instead of raising on construction.
CONTRACT_FIELDS = {field.name for field in CharacterContract._meta.fields}
CONTRACT_UPDATE_FIELDS = sorted(CONTRACT_FIELDS - {'contract_id'})


def refresh_character_contracts(character_id):
    """Upsert one character's contracts, then cache the names their ids carry.

    This feed never deletes, where orders and assets rewrite. ESI serves a
    30-day window, so a contract that leaves it is history no route can return.
    A contract two of our characters share upserts into the one row: the
    primary key is the contract, not the character.
    """
    try:
        contracts, response = esi.client.Contracts.GetCharactersCharacterIdContracts(
            character_id=character_id,
            token=Token.get_token(character_id, 'esi-contracts.read_character_contracts.v1')
        ).results(return_response=True)
    except HTTPNotModified as not_modified:
        logger.info("contracts unchanged for character %s, skipping", character_id)
        return _expires(not_modified.headers)

    rows = [
        CharacterContract(**{key: value for key, value in contract.model_dump().items()
                             if key in CONTRACT_FIELDS})
        for contract in contracts
    ]
    CharacterContract.objects.bulk_create(
        rows, update_conflicts=True, unique_fields=['contract_id'],
        update_fields=CONTRACT_UPDATE_FIELDS)
    logger.info("character %s contracts refreshed: %s rows", character_id, len(rows))
    # After the write on purpose: the contracts are safe even if a name route
    # fails, and a failure here still reaches the scheduler.
    names.resolve_contract_names(rows, character_id)
    return _expires(response.headers)


def refresh_character_assets(character_id):
    """Rewrite one character's CharacterAsset rows from ESI, storing the
    payload as it comes (station filtering happens at read time)."""
    try:
        assets, response = esi.client.Assets.GetCharactersCharacterIdAssets(
            character_id=character_id,
            token=Token.get_token(character_id, 'esi-assets.read_assets.v1')
        ).results(return_response=True)
    except HTTPNotModified as not_modified:
        logger.info("assets unchanged for character %s, skipping", character_id)
        return _expires(not_modified.headers)

    item_names = names.resolve_asset_names(
        [asset.item_id for asset in assets if asset.is_singleton], character_id)
    rows = [
        CharacterAsset(
            item_id=asset.item_id, character_id=character_id,
            type_id=asset.type_id, quantity=asset.quantity,
            location_id=asset.location_id, location_type=asset.location_type,
            location_flag=asset.location_flag, is_singleton=asset.is_singleton,
            is_blueprint_copy=getattr(asset, 'is_blueprint_copy', None),
            name=item_names.get(asset.item_id),
        )
        for asset in assets
    ]
    with transaction.atomic():
        CharacterAsset.objects.filter(character_id=character_id).delete()
        CharacterAsset.objects.bulk_create(rows)
    logger.info("character %s assets refreshed: %s rows", character_id, len(rows))
    return _expires(response.headers)

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
    correct no-op instead of a lost character.

    The route reports orders the character placed for the corporation too, and
    `is_corporation` says which. They are stored like any other: the character
    placed them, so they are ours on every page that reads this table.
    """
    try:
        orders, response = esi.client.Market.GetCharactersCharacterIdOrders(
            character_id=character_id,
            token=Token.get_token(character_id, 'esi-markets.read_character_orders.v1')
        ).results(return_response=True)
    except HTTPNotModified as not_modified:
        logger.info("orders unchanged for character %s, skipping", character_id)
        return _expires(not_modified.headers)

    rows = [
        CharacterOrder(order_id=order.order_id, character_id=character_id,
                       is_corporation=order.is_corporation)
        for order in orders
    ]
    # Three statements, and atomic so no failure can leave the character without
    # ownership rows. A plain delete would take the corporation feed's column
    # with it, because one order can be reported by both routes: this character
    # placed it and the corporation owns it. So the feed gives up its own
    # columns, drops the rows nobody owns any more, and claims the current set.
    with transaction.atomic():
        CharacterOrder.objects.filter(character_id=character_id).update(
            character_id=None, is_corporation=None)
        CharacterOrder.objects.filter(character_id=None, corporation_id=None).delete()
        CharacterOrder.objects.bulk_create(
            rows, update_conflicts=True, unique_fields=['order_id'],
            update_fields=['character_id', 'is_corporation'])
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


class CorporationUnknown(Exception):
    """No affiliation came back for the character serving a corporation feed."""


def _corporation_id(character_id):
    """The corporation the character belongs to.

    Resolved on every run from the public affiliation route, not stored: a
    character changes corporation without telling this app, and the route needs
    no token and no scope. ESI caches it for an hour, so the cost is nil.
    """
    affiliations = esi.client.Character.PostCharactersAffiliation(
        body=[character_id]).result()
    for entry in affiliations:
        entry = entry if isinstance(entry, dict) else entry.model_dump()
        if entry.get('character_id') == character_id:
            return entry['corporation_id']
    raise CorporationUnknown(f"no affiliation for character {character_id}")


# The seven corporation wallets. Every one is fetched: a division that starts
# trading must not go quiet, and 14 requests against a 3600 s cache is a
# fraction of the budget.
WALLET_DIVISIONS = range(1, 8)

# The character route owns character_id and is_personal, because it knows who
# executed the trade. A corporation write must leave both alone.
CORP_TRANSACTION_UPDATE_FIELDS = [
    'corporation_id', 'division', 'client_id', 'date', 'is_buy', 'journal_ref_id',
    'location_id', 'quantity', 'type_id', 'unit_price',
]
CORP_JOURNAL_UPDATE_FIELDS = [
    'corporation_id', 'division', 'amount', 'balance', 'date', 'description',
    'first_party_id', 'second_party_id', 'reason', 'ref_type', 'context_id',
    'context_id_type', 'tax', 'tax_receiver_id',
]


def _corporation_transactions(corporation_id, division, token):
    try:
        rows, response = esi.client.Wallet.GetCorporationsCorporationIdWalletsDivisionTransactions(
            corporation_id=corporation_id, division=division, token=token
        ).results(return_response=True)
    except HTTPNotModified as not_modified:
        return _expires(not_modified.headers)

    transactions = [
        MarketTransaction(
            corporation_id=corporation_id, division=division,
            # The corporation route carries no is_personal: the wallet is the
            # corporation's by definition. Set on insert only, so a row the
            # character route already wrote keeps what it reported.
            is_personal=False,
            **value)
        for value in (row.model_dump() for row in rows)
    ]
    MarketTransaction.objects.bulk_create(
        transactions, update_conflicts=True, unique_fields=['transaction_id'],
        update_fields=CORP_TRANSACTION_UPDATE_FIELDS)
    return _expires(response.headers)


def _corporation_journal(corporation_id, division, token):
    try:
        rows, response = esi.client.Wallet.GetCorporationsCorporationIdWalletsDivisionJournal(
            corporation_id=corporation_id, division=division, token=token
        ).results(return_response=True)
    except HTTPNotModified as not_modified:
        return _expires(not_modified.headers)

    entries = [
        WalletJournal(
            corporation_id=corporation_id, division=division,
            journal_id=value['id'],
            amount=value['amount'],
            balance=value['balance'],
            date=value['date'],
            ref_type=value['ref_type'],
            **{field: value[field] for field in OPTIONAL_JOURNAL_FIELDS if field in value},
        )
        for value in (row.model_dump() for row in rows)
    ]
    WalletJournal.objects.bulk_create(
        entries, update_conflicts=True, unique_fields=['journal_id'],
        update_fields=CORP_JOURNAL_UPDATE_FIELDS)
    return _expires(response.headers)


def refresh_corporation_wallet(character_id):
    """Journal and transactions for all seven wallets of the character's
    corporation. The later Expires wins, so the set repolls as one."""
    corporation_id = _corporation_id(character_id)
    token = Token.get_token(character_id, 'esi-wallet.read_corporation_wallets.v1')
    expirations = []
    for division in WALLET_DIVISIONS:
        expirations.append(_corporation_transactions(corporation_id, division, token))
        expirations.append(_corporation_journal(corporation_id, division, token))
    logger.info("corporation %s wallet refreshed over %s divisions",
                corporation_id, len(WALLET_DIVISIONS))
    expirations = [value for value in expirations if value is not None]
    return max(expirations) if expirations else None


def refresh_corporation_orders(character_id):
    """Rewrite the corporation's rows in CharacterOrder.

    Three statements rather than delete-then-insert: an order the corporation and
    one of our characters both report is one row, and this feed owns only
    corporation_id. So it gives up its column, drops the rows nobody owns any
    more, and claims the current set.
    """
    corporation_id = _corporation_id(character_id)
    try:
        orders, response = esi.client.Market.GetCorporationsCorporationIdOrders(
            corporation_id=corporation_id,
            token=Token.get_token(character_id, 'esi-markets.read_corporation_orders.v1')
        ).results(return_response=True)
    except HTTPNotModified as not_modified:
        logger.info("orders unchanged for corporation %s, skipping", corporation_id)
        return _expires(not_modified.headers)

    rows = [CharacterOrder(order_id=order.order_id, corporation_id=corporation_id)
            for order in orders]
    with transaction.atomic():
        CharacterOrder.objects.filter(corporation_id=corporation_id).update(
            corporation_id=None)
        CharacterOrder.objects.filter(character_id=None, corporation_id=None).delete()
        CharacterOrder.objects.bulk_create(
            rows, update_conflicts=True, unique_fields=['order_id'],
            update_fields=['corporation_id'])
    logger.info("corporation %s orders refreshed: %s rows", corporation_id, len(rows))
    return _expires(response.headers)


def refresh_corporation_assets(character_id):
    """Rewrite the corporation's CharacterAsset rows.

    A plain delete here, unlike the orders feed: an item belongs to a character
    hangar or to a corporation hangar, never to both, so no row is shared.
    """
    corporation_id = _corporation_id(character_id)
    try:
        assets, response = esi.client.Assets.GetCorporationsCorporationIdAssets(
            corporation_id=corporation_id,
            token=Token.get_token(character_id, 'esi-assets.read_corporation_assets.v1')
        ).results(return_response=True)
    except HTTPNotModified as not_modified:
        logger.info("assets unchanged for corporation %s, skipping", corporation_id)
        return _expires(not_modified.headers)

    # No names for corporation assets. The corporation names route rejects a
    # whole batch with 404 "Invalid IDs in the request" unless every id is a
    # nameable item: a fitted module, a stack or the office itself makes it fail,
    # while the character route answers "None" for those instead. Sorting the
    # nameable ids out needs a type taxonomy this feed has no other use for, and
    # a name is cosmetic - the type, the hangar division and the location all
    # still render. Verified against live ESI on 2026-08-17.
    rows = [
        CharacterAsset(
            item_id=asset.item_id, corporation_id=corporation_id,
            type_id=asset.type_id, quantity=asset.quantity,
            location_id=asset.location_id, location_type=asset.location_type,
            location_flag=asset.location_flag, is_singleton=asset.is_singleton,
            is_blueprint_copy=getattr(asset, 'is_blueprint_copy', None),
        )
        for asset in assets
    ]
    with transaction.atomic():
        CharacterAsset.objects.filter(corporation_id=corporation_id).delete()
        CharacterAsset.objects.bulk_create(rows)
    logger.info("corporation %s assets refreshed: %s rows", corporation_id, len(rows))
    return _expires(response.headers)


def refresh_corporation_contracts(character_id):
    """Upsert the corporation's contracts into the same table the characters use.

    The table has no owner column - a contract is one global object and the
    payload names every party - so this feed adds rows and never deletes, exactly
    as the character one does.
    """
    corporation_id = _corporation_id(character_id)
    try:
        contracts, response = esi.client.Contracts.GetCorporationsCorporationIdContracts(
            corporation_id=corporation_id,
            token=Token.get_token(character_id, 'esi-contracts.read_corporation_contracts.v1')
        ).results(return_response=True)
    except HTTPNotModified as not_modified:
        logger.info("contracts unchanged for corporation %s, skipping", corporation_id)
        return _expires(not_modified.headers)

    rows = [
        CharacterContract(**{key: value for key, value in contract.model_dump().items()
                             if key in CONTRACT_FIELDS})
        for contract in contracts
    ]
    CharacterContract.objects.bulk_create(
        rows, update_conflicts=True, unique_fields=['contract_id'],
        update_fields=CONTRACT_UPDATE_FIELDS)
    logger.info("corporation %s contracts refreshed: %s rows", corporation_id, len(rows))
    names.resolve_contract_names(rows, character_id)
    return _expires(response.headers)

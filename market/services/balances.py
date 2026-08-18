"""The wallet balances the header shows, held in the cache.

ESI reports a balance as one number per wallet. No table needs it: the figure is
a header display, the next wallet feed run replaces it, and nothing else reads
it. So the wallet feeds write it to the cache and the context processor sums it.

Deriving it from `WalletJournal.balance` instead was rejected. That column holds
the balance after the newest *stored* row, which is the balance at the last ISK
movement rather than now, and a wallet that has been idle a while reports a
figure that is wrong rather than merely old.
"""
from django.core.cache import cache

# Three wallet-feed cycles, which run hourly. One missed run must not blank the
# header, and a feed that stays dead already shows in the fetch warning bar.
CACHE_SECONDS = 3 * 3600


def _character_key(character_id):
    return f'wallet_balance:character:{character_id}'


def _corporation_key(corporation_id):
    return f'wallet_balance:corporation:{corporation_id}'


def store_character(character_id, balance):
    cache.set(_character_key(character_id), float(balance), CACHE_SECONDS)


def store_corporation(corporation_id, balance):
    cache.set(_corporation_key(corporation_id), float(balance), CACHE_SECONDS)


def total(character_ids, corporation_ids):
    """The sum of the balances held for these wallets, or None when none is held.

    A wallet with nothing cached contributes nothing and says nothing. A fresh
    Redis, a newly tracked wallet and a wallet whose balance call failed all look
    the same, and all resolve themselves on the next feed run. None rather than
    zero when nothing at all is cached, so the header can omit the figure instead
    of claiming the wallets are empty.
    """
    keys = ([_character_key(value) for value in character_ids]
            + [_corporation_key(value) for value in corporation_ids])
    if not keys:
        return None
    held = cache.get_many(keys)
    return sum(held.values()) if held else None

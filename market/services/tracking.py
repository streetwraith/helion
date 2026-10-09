"""What the tracking block on the characters page reads and writes.

TrackedCharacter says which feeds the ESI fetch scheduler runs for a character.
The scheduler reconciles EsiFetchState against it on every watchdog tick, so a
save here writes one row and nothing else: the tick creates or deletes the fetch
state, and the initial delay spreads the first round.

A row is keyed by character_id. EsiFetchState is keyed by name, so a save also
stores the current name, and after an EVE rename the next tick moves the fetch
state to the new name.
"""
from django.utils import timezone

from esi.models import Token
from market.models import (
    CharacterAsset, CharacterOrder, EsiFetchState, MarketTransaction,
    TrackedCharacter, WalletJournal)
from market.services.esi_scheduler import FEED_SCOPES, FEEDS, reenable


# How much of disabled_reason the block shows. Long enough for the status code and
# the start of the message ESI sent.
REASON_LENGTH = 120


# The tables a corporation feed writes. A corporation is "ours" when one of them
# names it.
CORPORATION_TABLES = (CharacterOrder, CharacterAsset, MarketTransaction, WalletJournal)


def corporation_ids():
    """Every corporation the app holds data for.

    Read off the rows rather than from a list of tracked corporations: the
    corporation feeds are tags on a character, so nothing else records which
    corporations arrived. One indexed distinct per table, each answering a handful
    of ids.

    All four tables, not just the two the trade hub reads: the contracts page
    needs the same answer, and a corporation's contracts arrive with
    `for_corporation` false - that flag means "issued on behalf of the
    corporation", not "the corporation is a party" (verified on live data).
    """
    ids = set()
    for model in CORPORATION_TABLES:
        ids |= set(model.objects.exclude(corporation_id=None)
                   .values_list('corporation_id', flat=True).distinct())
    return ids


def trader_character_ids():
    """The character ids whose wallet the profit statistics count.

    Read off the rows, not through the tokens: a deleted character loses its
    tokens, and its stored history must still count.
    """
    return set(TrackedCharacter.objects.filter(is_trader=True)
               .values_list('character_id', flat=True))


def wallet_balance_owner_ids():
    """(character ids, corporation ids) whose balance the header sums.

    The characters come from TrackedCharacter, because only a running wallet feed
    ever writes a balance. Matching on `track_list()` rather than the raw `tracks`
    string, so `corp_wallet` alone does not read as a personal wallet feed.

    The corporations come off the stored rows, not from the affiliation route:
    that route costs an ESI call and this answer feeds every page. A corporation
    with no wallet feed therefore appears here and simply has nothing cached.
    """
    character_ids = {tracked.character_id for tracked
                     in TrackedCharacter.objects.only('character_id', 'tracks')
                     if 'wallet' in tracked.track_list()}
    return character_ids, corporation_ids()


def is_trader(character_id):
    """Whether the statistics count this character. An untracked one is not.

    False rather than the field default: `trader_character_ids` reads rows, so a
    character with no row counts for nothing and the checkbox must say so.
    """
    tracked = TrackedCharacter.objects.filter(character_id=character_id).first()
    return tracked.is_trader if tracked else False


def _authorised_scopes(character_id):
    """Every scope the character holds, across all of its tokens.

    The union, not the newest token's set: Token.get_token searches all of them,
    so a feed an older token can still serve must not read as unauthorised.
    """
    return set(Token.objects.filter(character_id=character_id)
               .values_list('scopes__name', flat=True))


def get_feed_rows(character_id, character_name):
    """One row per feed for the block: what is ticked, what may be, how it fares."""
    tracked = TrackedCharacter.objects.filter(character_id=character_id).first()
    tracks = set(tracked.track_list()) if tracked else set()
    scopes = _authorised_scopes(character_id)
    # The stored name, not the token's: after a rename the fetch state keeps
    # the old name until the next save.
    state_name = tracked.character_name if tracked else character_name
    states = {state.feed: state for state
              in EsiFetchState.objects.filter(character_name=state_name)}

    now = timezone.now()
    rows = []
    for feed in FEEDS:
        scope = FEED_SCOPES[feed]
        state = states.get(feed)
        rows.append({
            'feed': feed,
            'scope': scope,
            'authorised': scope in scopes,
            'tracked': feed in tracks,
            'state': state,
            'disabled': state is not None and state.disabled_at is not None,
            # Trimmed: disabled_reason holds a repr of the last error, and the
            # useful part is at the front. A corporation feed usually dies on a
            # missing in-corp role, which nothing else on the page would show.
            'reason': (state.disabled_reason or '')[:REASON_LENGTH] if state else '',
            # A null next_due means "on the next tick", which no duration filter
            # can render.
            'due_now': state is not None and (state.next_due is None
                                              or state.next_due <= now),
        })
    return rows


def save_tracks(character_id, character_name, feeds, trader):
    """Store the ticked feeds and the trader flag.

    An unauthorised feed is refused here and not only in the template: a disabled
    checkbox is a browser convention, and arming a feed that cannot work would
    spend the error budget three times before the row hard-disabled itself.

    Unknown tags are dropped, which is what the scheduler already does with them.

    The row survives with no feed ticked, because it also carries `is_trader`.
    Deleting it would drop the character from the profit statistics without
    saying so. A row with no tags asks the scheduler for nothing.

    The name is written on every save, which is how an EVE rename reaches the
    fetch state: the next tick drops the rows under the old name and creates
    them under the new one.
    """
    scopes = _authorised_scopes(character_id)
    wanted = [feed for feed in FEEDS
              if feed in feeds and FEED_SCOPES[feed] in scopes]
    tracked, _ = TrackedCharacter.objects.update_or_create(
        character_id=character_id,
        defaults={'character_name': character_name,
                  'tracks': ', '.join(wanted), 'is_trader': trader})
    return tracked


def reenable_feed(character_id, feed):
    """Clear one feed's failure state. Unknown feeds and untracked characters
    change nothing."""
    tracked = TrackedCharacter.objects.filter(character_id=character_id).first()
    if feed not in FEEDS or tracked is None:
        return 0
    return reenable(EsiFetchState.objects.filter(
        character_name=tracked.character_name, feed=feed))

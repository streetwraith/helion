"""What the tracking block on the characters page reads and writes.

TrackedCharacter says which feeds the ESI fetch scheduler runs for a character.
The scheduler reconciles EsiFetchState against it on every watchdog tick, so a
save here writes one row and nothing else: the tick creates or deletes the fetch
state, and the initial delay spreads the first round.

A row is keyed by character_name, as EsiFetchState is. An EVE rename therefore
orphans both, and fetching stops with no error anywhere.
"""
from django.utils import timezone

from esi.models import Token
from market.models import EsiFetchState, TrackedCharacter
from market.services.esi_scheduler import FEED_SCOPES, FEEDS, reenable


def _authorised_scopes(character_id):
    """Every scope the character holds, across all of its tokens.

    The union, not the newest token's set: Token.get_token searches all of them,
    so a feed an older token can still serve must not read as unauthorised.
    """
    return set(Token.objects.filter(character_id=character_id)
               .values_list('scopes__name', flat=True))


def get_feed_rows(character_id, character_name):
    """One row per feed for the block: what is ticked, what may be, how it fares."""
    tracked = TrackedCharacter.objects.filter(character_name=character_name).first()
    tracks = set(tracked.track_list()) if tracked else set()
    scopes = _authorised_scopes(character_id)
    states = {state.feed: state for state
              in EsiFetchState.objects.filter(character_name=character_name)}

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
            # A null next_due means "on the next tick", which no duration filter
            # can render.
            'due_now': state is not None and (state.next_due is None
                                              or state.next_due <= now),
        })
    return rows


def save_tracks(character_id, character_name, feeds):
    """Store the ticked feeds, and drop the row when none are ticked.

    An unauthorised feed is refused here and not only in the template: a disabled
    checkbox is a browser convention, and arming a feed that cannot work would
    spend the error budget three times before the row hard-disabled itself.

    Unknown tags are dropped, which is what the scheduler already does with them.
    """
    scopes = _authorised_scopes(character_id)
    wanted = [feed for feed in FEEDS
              if feed in feeds and FEED_SCOPES[feed] in scopes]
    if not wanted:
        TrackedCharacter.objects.filter(character_name=character_name).delete()
        return None
    tracked, _ = TrackedCharacter.objects.update_or_create(
        character_name=character_name, defaults={'tracks': ', '.join(wanted)})
    return tracked


def reenable_feed(character_name, feed):
    """Clear one feed's failure state. Unknown feed names change nothing."""
    if feed not in FEEDS:
        return 0
    return reenable(EsiFetchState.objects.filter(
        character_name=character_name, feed=feed))

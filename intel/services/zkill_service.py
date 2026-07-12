"""zKillboard access.

zKill's `kills/characterID/{id}/` endpoint returns fully enriched killmails
inline (victim, attackers, zkb), ~30 per page, so no per-killmail ESI call is
needed in the common case. Requests must carry a descriptive User-Agent and be
spaced out; pagination is bounded and incremental (stop at the time cutoff or
the first already-cached killmail).
"""

MAX_PAGES = 100  # hard bound on pagination per character


def fetch_character_kills(character_id: int, since_days: int) -> list[dict]:
    """Return enriched killmails where the character was an attacker."""
    raise NotImplementedError

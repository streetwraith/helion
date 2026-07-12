"""Public (tokenless) ESI lookups used by threat profiling.

Name resolution, affiliations and killmail fetch are all public endpoints, so
this feature is decoupled from the SSO/scope flow entirely.
"""


def resolve_names_to_ids(names: list[str]) -> dict[str, int]:
    """Resolve character names to character_ids (ESI POST /universe/ids/)."""
    raise NotImplementedError


def fetch_affiliations(character_ids: list[int]) -> dict[int, dict]:
    """Batch corp/alliance/faction lookup (ESI POST /characters/affiliation/)."""
    raise NotImplementedError

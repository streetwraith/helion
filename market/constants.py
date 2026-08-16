REGION_ID_DOMAIN = 10000043
REGION_ID_FORGE = 10000002

# Market group roots the station trading filter never offers: nothing under
# them is worth scanning for a spread. Ids, not names, because a name in the
# sde can change with an expansion.
NON_TRADED_MARKET_GROUP_ROOTS = (
    1396,  # Apparel
    2,     # Blueprints & Reactions
    3628,  # Personalization
    1922,  # Pilot's Services
    1954,  # Ship SKINs
    150,   # Skills
    1659,  # Special Edition Assets
)

# Player structures start here and NPC stations sit far below it, with nothing
# in between in the order data. Classifying by the id rather than by a missing
# name keeps a station the SDE fails to name out of the structure filter.
FIRST_STRUCTURE_ID = 1_000_000_000_000

PLEX_TYPE_ID = 44992
# PLEX trades on a global market and never appears in normal region order
# feeds; it is stored as this pseudo-region (GPMR-01). Its orders sit in
# stations across the whole universe, so never apply a hub-range filter.
GLOBAL_PLEX_MARKET_REGION_ID = 19000001

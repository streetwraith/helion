"""Ship categorization for threat profiling.

These map an EVE ship to a profile bucket. The plan is to key on SDE group_id
where a whole group qualifies (e.g. every Exhumer) and fall back to explicit
type_id for individual exceptions.

PLACEHOLDER VALUES ONLY — the curated lists are to be supplied by the user.
The demo UI does not use these; it renders intel/mock_data.py.
"""

# group_id -> victim bucket. (Examples below are illustrative, not final.)
VICTIM_GROUP_BUCKETS: dict[int, str] = {
    # 463: 'big_miner',    # Mining Barge
    # 543: 'big_miner',    # Exhumer
    # 25:  'combat',       # Frigate (needs type-level split for mining/explo frigs)
}

# Explicit type_id overrides where a group is ambiguous (e.g. mining/explo frigates).
VICTIM_TYPE_BUCKETS: dict[int, str] = {
    # 32880: 'small_miner',  # Venture
}

# Ships that can fit a Covert Ops Cloaking Device — count toward "stealth" use.
# NOTE: T3 Cruisers only cloak with the right subsystem; the killmail shows only
# the hull, so all T3Cs are counted as stealth-capable unless excluded here.
STEALTH_GROUP_IDS: set[int] = {
    # 834,  # Stealth Bomber
    # 830,  # Covert Ops
    # 833,  # Force Recon Ship
    # 906,  # Combat Recon Ship
    # 963,  # Strategic Cruiser (T3C)
    # 1202, # Blockade Runner
}

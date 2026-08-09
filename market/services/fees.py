"""Trading fee rates and the derived sale-proceeds constant."""

def get_brokers_fee(faction_standing=9.75, corporation_standing=10.0, broker_relations_level=5):
    return 0.03 - (0.003 * broker_relations_level) - (0.0003 * faction_standing) - (0.0002 * corporation_standing)

def get_sales_tax():
    return 0.0337

# Percent of a sell price kept after tax and fees, used by the hauling and LP
# profit math. Kept at the author's hand-picked 96.4 even though the functions
# above give 100 * (1 - 0.0337 - 0.010075) = 95.62 -- deriving it from them
# would change every displayed hauling and LP profit.
SALE_PROCEEDS_PERCENT = 96.4

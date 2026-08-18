"""Ice hauling data: hull sizes, product type ids, and reprocessing yield tables."""

# The SDE inventory groups that define the ice business: ice, compressed and not,
# and the seven products it reprocesses into. Wider than ICE_TYPES below, which
# fixes the yield table and holds compressed ice only.
ICE_GROUP_IDS = (465, 423)

# The corporation that runs the reprocessing structure used for ice. A
# reprocessing_tax row names the owner of the facility and never the structure
# itself, so this is the finest filter the wallet journal allows.
ICE_REFINING_CORPORATION_ID = 98671032

# Collateral paid out on two failed ice couriers, pinned by journal id. A payout
# names no item, so a later one could be for anything; pinning keeps a non-ice
# payout out of the ice sales total.
ICE_COLLATERAL_PAYOUT_JOURNAL_IDS = (24325786604, 24325786992)

FREIGHTER_HULL_CAPACITY = {
    'fenrir': 435000,
    'charon': 465000,
    'obelisk': 440000,
    'providence': 435000
}

ICE_PRODUCT_TYPES = {
    'Heavy Water': 16272,
    'Liquid Ozone': 16273, 
    'Strontium Clathrates': 16275, 
    'Helium Isotopes': 16274, 
    'Nitrogen Isotopes': 17888, 
    'Oxygen Isotopes': 17887, 
    'Hydrogen Isotopes': 17889,
}

ICE_TYPES = {
    'Compressed Clear Icicle': { 'type_id': 28434, 'base_yield': {
        'Heavy Water': 69,
        'Liquid Ozone': 35, 
        'Strontium Clathrates': 1, 
        'Helium Isotopes': 414, 
        'Nitrogen Isotopes': 0, 
        'Oxygen Isotopes': 0, 
        'Hydrogen Isotopes': 0,
    }},
    'Compressed White Glaze': { 'type_id': 28444, 'base_yield': {
        'Heavy Water': 69,
        'Liquid Ozone': 35, 
        'Strontium Clathrates': 1, 
        'Helium Isotopes': 0, 
        'Nitrogen Isotopes': 414, 
        'Oxygen Isotopes': 0, 
        'Hydrogen Isotopes': 0,
    }},
    'Compressed Blue Ice': { 'type_id': 28433, 'base_yield': {
        'Heavy Water': 69,
        'Liquid Ozone': 35, 
        'Strontium Clathrates': 1, 
        'Helium Isotopes': 0, 
        'Nitrogen Isotopes': 0, 
        'Oxygen Isotopes': 414, 
        'Hydrogen Isotopes': 0,
    }},
    'Compressed Glacial Mass': { 'type_id': 28438, 'base_yield': {
        'Heavy Water': 69,
        'Liquid Ozone': 35, 
        'Strontium Clathrates': 1, 
        'Helium Isotopes': 0, 
        'Nitrogen Isotopes': 0, 
        'Oxygen Isotopes': 0, 
        'Hydrogen Isotopes': 414,
    }},
    'Compressed Enriched Clear Icicle': { 'type_id': 28436, 'base_yield': {
        'Heavy Water': 104,
        'Liquid Ozone': 55, 
        'Strontium Clathrates': 1, 
        'Helium Isotopes': 483, 
        'Nitrogen Isotopes': 0, 
        'Oxygen Isotopes': 0, 
        'Hydrogen Isotopes': 0,
    }},
    'Compressed Pristine White Glaze': { 'type_id': 28441, 'base_yield': {
        'Heavy Water': 104,
        'Liquid Ozone': 55, 
        'Strontium Clathrates': 1, 
        'Helium Isotopes': 0, 
        'Nitrogen Isotopes': 483, 
        'Oxygen Isotopes': 0, 
        'Hydrogen Isotopes': 0,
    }},
    'Compressed Thick Blue Ice': { 'type_id': 28443, 'base_yield': {
        'Heavy Water': 104,
        'Liquid Ozone': 55, 
        'Strontium Clathrates': 1, 
        'Helium Isotopes': 0, 
        'Nitrogen Isotopes': 0, 
        'Oxygen Isotopes': 483, 
        'Hydrogen Isotopes': 0,
    }},
    'Compressed Smooth Glacial Mass': { 'type_id': 28442, 'base_yield': {
        'Heavy Water': 104,
        'Liquid Ozone': 55, 
        'Strontium Clathrates': 1, 
        'Helium Isotopes': 0, 
        'Nitrogen Isotopes': 0, 
        'Oxygen Isotopes': 0, 
        'Hydrogen Isotopes': 483,
    }},
    'Compressed Glare Crust': { 'type_id': 28439, 'base_yield': {
        'Heavy Water': 1381,
        'Liquid Ozone': 691, 
        'Strontium Clathrates': 35, 
        'Helium Isotopes': 0, 
        'Nitrogen Isotopes': 0, 
        'Oxygen Isotopes': 0, 
        'Hydrogen Isotopes': 0,
    }},
    'Compressed Dark Glitter': { 'type_id': 28435, 'base_yield': {
        'Heavy Water': 691,
        'Liquid Ozone': 1381, 
        'Strontium Clathrates': 69, 
        'Helium Isotopes': 0, 
        'Nitrogen Isotopes': 0, 
        'Oxygen Isotopes': 0, 
        'Hydrogen Isotopes': 0,
    }},
    'Compressed Gelidus': { 'type_id': 28437, 'base_yield': {
        'Heavy Water': 345,
        'Liquid Ozone': 691, 
        'Strontium Clathrates': 104, 
        'Helium Isotopes': 0, 
        'Nitrogen Isotopes': 0, 
        'Oxygen Isotopes': 0, 
        'Hydrogen Isotopes': 0,
    }},
    'Compressed Krystallos': { 'type_id': 28440, 'base_yield': {
        'Heavy Water': 173,
        'Liquid Ozone': 691, 
        'Strontium Clathrates': 173, 
        'Helium Isotopes': 0, 
        'Nitrogen Isotopes': 0, 
        'Oxygen Isotopes': 0, 
        'Hydrogen Isotopes': 0,
    }},
}

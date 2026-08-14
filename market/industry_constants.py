"""The Helium Fuel Block blueprint: base materials, output and the ME assumption.

The sde schema carries no blueprint table, so the recipe lives here, like the
reprocessing yields in ice_constants.py. The quantities are the blueprint's own
base numbers for one run, before material efficiency, so they stay comparable
with what the game shows on the blueprint itself.
"""

PRODUCT_TYPE_ID = 4247  # Helium Fuel Block
# Units one run produces. Equals the product's sde portion_size.
OUTPUT_QUANTITY = 40
MATERIAL_EFFICIENCY = 10

# type_id -> base units for one run. The order is the stack order of the chart:
# largest share of the cost first, so the thick bands sit at the bottom and the
# hairlines at the top.
BASE_MATERIALS = {
    16274: 450,  # Helium Isotopes
    9832: 9,     # Coolant
    16275: 20,   # Strontium Clathrates
    9848: 1,     # Robotics
    16272: 470,  # Heavy Water
    16273: 350,  # Liquid Ozone
    44: 4,       # Enriched Uranium
    3689: 4,     # Mechanical Parts
    3683: 22,    # Oxygen
}

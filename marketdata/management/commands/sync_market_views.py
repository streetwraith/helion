from django.core.management.base import BaseCommand
from django.db import connection, transaction

# The single source of truth for the orders_hub view. Not a migration: in the
# test database, migrations run before conftest creates the market tables, so
# a migration-based CREATE VIEW can never work there. Tests and deploys both
# apply it through this command (tests via call_command).
#
# Names are deliberately unqualified: the view and the helion tables resolve
# through the connection's search_path (helion schema in dev/prod, public in
# the test database). market.orders is schema-qualified because marketmanager
# owns it everywhere.
#
# The inner JOIN to market_tradehub restricts the view to the trade hub
# regions. market.orders carries 25 regions; several call sites have no
# region filter, so without this restriction their meaning silently changes.
#
# A buy order in a system with no jump row gets a NULL flag (the ELSE branch
# compares against NULL), which every `= TRUE` filter treats as out of range.
# This is deliberate; recompute_hub_jumps warns when coverage is incomplete.
ORDERS_HUB_VIEW_SQL = """
CREATE VIEW orders_hub AS
SELECT o.*,
       (CASE WHEN o.location_id = h.station_id THEN true
             WHEN NOT o.is_buy_order           THEN false
             WHEN o."range" = 'region'         THEN true
             WHEN o."range" = 'station'        THEN false
             WHEN o."range" = 'solarsystem'    THEN o.system_id = h.system_id
             ELSE o."range"::int >= j.jumps_to_trade_hub
        END) AS is_in_trade_hub_range
FROM market.orders o
JOIN market_tradehub h ON h.region_id = o.region_id
LEFT JOIN market_systemhubjumps j ON j.system_id = o.system_id
"""


class Command(BaseCommand):
    help = "Create or replace the helion-owned views over the market schema."

    def handle(self, *args, **options):
        # DROP + CREATE instead of CREATE OR REPLACE: REPLACE refuses column
        # additions (o.* is expanded at creation time), DROP + CREATE in one
        # transaction stays idempotent when market.orders grows a column.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("DROP VIEW IF EXISTS orders_hub")
            cursor.execute(ORDERS_HUB_VIEW_SQL)
        self.stdout.write("orders_hub view synced.")

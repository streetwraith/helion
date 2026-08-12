from django.core.management.base import BaseCommand

from helion.providers import esi
from market.models import SystemHubJumps, TradeHub
from evesde.models import MapSolarSystem


def missing_jump_coverage():
    """Hub-region systems without a jump row, as {hub name: count}.

    The orders_hub view treats a buy order in a system with no jump row as
    out of range, so incomplete coverage silently hides orders.
    """
    covered = set(SystemHubJumps.objects.values_list("system_id", flat=True))
    missing = {}
    for hub in TradeHub.objects.all():
        count = MapSolarSystem.objects.filter(
            region_id=hub.region_id).exclude(system_id__in=covered).count()
        if count:
            missing[hub.name] = count
    return missing


class Command(BaseCommand):
    help = (
        "Rebuild SystemHubJumps: stargate jumps from each solar system in a "
        "trade-hub region to that hub, via ESI /route. This is static reference "
        "data (map topology + hub set), so run it after a TradeHub change or an "
        "SDE-map update -- not on the market-refresh cadence."
    )

    def handle(self, *args, **options):
        rows = []
        for hub in TradeHub.objects.all():
            if hub.system_id is None:
                self.stdout.write(f"{hub.name}: no system_id, skipping")
                continue
            systems = MapSolarSystem.objects.filter(region_id=hub.region_id)
            self.stdout.write(f"{hub.name}: {systems.count()} systems in region {hub.region_id}")
            for system in systems:
                # The compatibility-date API replaced GET /route/{o}/{d} with
                # POST /route; an empty body keeps the old "shortest" default.
                route = esi.client.Routes.PostRoute(
                    body={}, origin_system_id=system.system_id, destination_system_id=hub.system_id
                ).result(use_etag=False)
                rows.append(SystemHubJumps(
                    system_id=system.system_id, jumps_to_trade_hub=len(route.route) - 1))

        SystemHubJumps.objects.all().delete()
        SystemHubJumps.objects.bulk_create(rows)
        self.stdout.write(self.style.SUCCESS(f"recomputed {len(rows)} rows"))

        for hub_name, count in missing_jump_coverage().items():
            self.stderr.write(self.style.WARNING(
                f"{hub_name}: {count} systems in the hub region have no jump row"))

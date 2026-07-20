from django.core.management.base import BaseCommand

from market.models import TradeItem
from evesde.models import Type


class Command(BaseCommand):
    help = "Fill missing name/group_id/market_group_id on TradeItem from the sde schema (denormalized cache)."

    def handle(self, *args, **options):
        to_save = []
        for trade_item in TradeItem.objects.all():
            if not trade_item.name or not trade_item.group_id or not trade_item.market_group_id:
                sde_type = Type.objects.get(type_id=trade_item.type_id)
                trade_item.name = sde_type.name
                trade_item.group_id = sde_type.group_id
                trade_item.market_group_id = sde_type.market_group_id
                to_save.append(trade_item)
        if to_save:
            TradeItem.objects.bulk_update(to_save, fields=["name", "group_id", "market_group_id"])
        self.stdout.write(self.style.SUCCESS(f"updated {len(to_save)} trade items"))

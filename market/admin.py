from django.contrib import admin
from .models import (
    MarketOrder, MarketTransaction, MarketRegionStatus, TradeItem, TradeHub, TrackedCharacter,
)

admin.site.register(MarketOrder)
admin.site.register(MarketTransaction)
admin.site.register(MarketRegionStatus)
admin.site.register(TradeItem)
admin.site.register(TradeHub)
admin.site.register(TrackedCharacter)
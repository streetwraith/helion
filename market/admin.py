from django.contrib import admin
from .models import AuthData, Character, MarketOrder, MarketTransaction, MarketRegionStatus, TradeItem, TradeHub

admin.site.register(AuthData)
admin.site.register(Character)
admin.site.register(MarketOrder)
admin.site.register(MarketTransaction)
admin.site.register(MarketRegionStatus)
admin.site.register(TradeItem)
admin.site.register(TradeHub)
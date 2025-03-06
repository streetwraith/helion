from market.models import MarketRegionStatus, TradeHub

def global_site_data(request):
    trade_hubs = TradeHub.objects.all()
    context = { "trade_hubs": list(trade_hubs) }
    return context
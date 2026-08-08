from market.models import MarketRegionStatus, TradeHub
from market.services import market_service

def global_site_data(request):
    trade_hubs = TradeHub.objects.all()
    context = { "trade_hubs": list(trade_hubs) }
    if request.user.is_authenticated:
        context["price_ticker"] = market_service.get_price_ticker()
    return context

from django.urls import path

from .views import (
    transaction_history,
    market_open_in_game,
    trade_item_add_or_del,
    index,
    shopping_list,
    market_hauling_index,
    market_hauling_sell_to_buy,
    market_hauling_sell_to_sell,
    lp_index,
    lp_data,
    market_trade_hub,
    market_transactions,
    market_trade_hub_mistakes,
    market_ice_index,
    market_history,
    type_search,
    transactions_since,
)

urlpatterns = [
    # base
    path("", index, name="market_index"),
    path("shopping_list", shopping_list, name="shopping_list"),

    # station trading
    path("trade_hub/<int:region_id>", market_trade_hub, name="market_trade_hub"),
    path("trade_hub/<int:region_id>/mistakes", market_trade_hub_mistakes, name="market_trade_hub_mistakes"),
    
    # transactions
    path("transactions", market_transactions, name="market_transactions"),

    # history chart
    path("history", market_history, name="market_history"),

    # hauling
    path("hauling", market_hauling_index, name="market_hauling_index"),
    path("hauling_stb/<str:from_location>/<str:to_location>", market_hauling_sell_to_buy, name="market_hauling_sell_to_buy"),
    path("hauling_sts/<str:from_location>/<str:to_location>", market_hauling_sell_to_sell, name="market_hauling_sell_to_sell"),

    # ice
    path("ice", market_ice_index, name="market_ice_index"),

    # loyalty points
    path("lp", lp_index, name="lp_index"),
    path("lp/<str:trade_type>/<str:location>/<str:corporation_name>", lp_data, name="lp_data"),
    
    # ajax
    path("ajax/market_open_in_game", market_open_in_game, name="market_open_in_game"),
    path("ajax/trade_item_add_or_del", trade_item_add_or_del, name="trade_item_add_or_del"),
    path("ajax/transaction_history", transaction_history, name="transaction_history"),
    path("ajax/type_search", type_search, name="type_search"),
    path("ajax/transactions_since", transactions_since, name="transactions_since"),
]
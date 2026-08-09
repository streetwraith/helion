"""Facade over the market service modules.

Call sites import this module and keep working unchanged; the
implementations live in esi_sync, orders, history, wallet and fees.
"""
from market.services.esi_sync import (
    GLOBAL_PLEX_MARKET_REGION_ID,
    PLEX_TYPE_ID,
    fetch_market_orders_page,
    fetch_market_orders_parallel,
    fetch_plex_best_ask,
    get_character_assets,
    get_wallet_journal,
    process_market_orders,
    refresh_all_trade_hub_orders,
    refresh_trade_hub_orders,
    save_market_orders,
    update_market_history,
    update_market_transactions,
)
from market.services.fees import (
    SALE_PROCEEDS_PERCENT,
    get_brokers_fee,
    get_sales_tax,
)
from market.services.history import (
    _price_distance,
    calculate_market_history_average_volume,
    calculate_market_history_averages,
    get_a4e_market_history_volume,
    get_average_daily_volume_bulk,
    get_market_history,
    get_market_history_for_types,
)
from market.services.orders import (
    JITA_STATION_ID,
    LARGE_SKILL_INJECTOR_TYPE_ID,
    PRICE_TICKER_CACHE_SECONDS,
    SKILL_EXTRACTOR_TYPE_ID,
    find_type_ids_by_market_groups,
    find_undercut_buy_orders,
    find_undercut_sell_orders,
    get_jita_best_ask,
    get_orders_in_hub_range,
    get_price_ticker,
    get_shopping_list_prices,
    save_market_order_undercuts,
    trade_item_add,
    trade_item_del,
)
from market.services.wallet import (
    get_average_transaction_price,
    get_average_transaction_price_bulk,
    get_market_transactions,
    get_trade_history,
    get_trade_history_bulk,
)

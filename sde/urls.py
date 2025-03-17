from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="sde_index"),
    path("import/type_ids", views.import_sde_type_ids, name="import_sde_type_ids"),
    path("import/type_materials", views.import_type_materials, name="import_type_materials"),
    path("import/market_groups", views.import_sde_market_groups, name="import_sde_market_groups"),
    path("import/npc_corporations", views.import_npc_corporations, name="import_npc_corporations"),
    path("import/solar_systems", views.import_solar_systems, name="import_solar_systems"),
    path("import/update_jumps_to_trade_hub", views.update_jumps_to_trade_hub, name="update_jumps_to_trade_hub"),
    path("sync/trade_items", views.sync_trade_items_data, name="sync_trade_items_data"),
]
from django.shortcuts import render, redirect
import yaml
from sde.models import SdeTypeId, MarketGroup, NpcCorporation, SolarSystem
from market.models import TradeItem, TradeHub
from market.services import market_service
import os
from django.conf import settings
from pathlib import Path
import re
from helion.providers import esi

def index(request):
    context = {}
    return render(request, "sde/index.html", context)

def import_npc_corporations(request):
    file_path = os.path.join(settings.BASE_DIR, 'sde/sde/fsd/npcCorporations.yaml')
    with open(file_path, 'r') as file:
        data = yaml.safe_load(file)
        npc_corporations = []
        for key, value in data.items():
            if 'nameID' in value:
                npc_corporation = NpcCorporation(corporation_id=key, name=value['nameID']['en'])
                if 'factionID' in value:
                    npc_corporation.faction_id = value['factionID']
                npc_corporations.append(npc_corporation)

    NpcCorporation.objects.bulk_create(npc_corporations, 
        update_conflicts=True, 
        unique_fields=['corporation_id'], 
        update_fields=['name', 'faction_id'])

    return redirect('sde_index')

def import_sde_type_ids(request):
    file_path = os.path.join(settings.BASE_DIR, 'sde/sde/fsd/types.yaml')
    with open(file_path, 'r') as file:
        data = yaml.safe_load(file)
        type_ids = []
        for key, value in data.items():
            if 'groupID' in value and 'name' in value:
                type_id = SdeTypeId(type_id=key, group_id=value['groupID'], name=value['name']['en'])
                if 'marketGroupID' in value:
                    type_id.market_group_id = value['marketGroupID']
                if 'metaGroupID' in value:
                    type_id.meta_id = value['metaGroupID']
                if 'volume' in value:
                    type_id.volume = value['volume']
                type_ids.append(type_id)

    SdeTypeId.objects.bulk_create(type_ids, 
        update_conflicts=True, 
        unique_fields=['type_id'], 
        update_fields=['group_id', 'market_group_id', 'name', 'meta_id', 'volume'])

    return redirect('sde_index')

def import_sde_market_groups(request):
    file_path = os.path.join(settings.BASE_DIR, 'sde/sde/fsd/marketGroups.yaml')
    with open(file_path, 'r') as file:
        data = yaml.safe_load(file)
        market_groups = []
        for key, value in data.items():
            if 'nameID' in value:
                market_group = MarketGroup(market_group_id=key, name=value['nameID']['en'], has_types=value['hasTypes'])
                if 'parentGroupID' in value:
                    market_group.parent_group_id = value['parentGroupID']
                if 'descriptionID' in value:
                    market_group.description = value['descriptionID']['en']
                market_groups.append(market_group)

    MarketGroup.objects.bulk_create(market_groups, 
        update_conflicts=True, 
        unique_fields=['market_group_id'], 
        update_fields=['name', 'description', 'has_types', 'parent_group_id'])

    return redirect('sde_index')

def import_solar_systems(request):
    solar_systems = []
    for region_name in ['Domain', 'TheForge', 'Heimatar', 'Metropolis', 'SinqLaison']:
        print(f'region {region_name}')
        region_directory_path = Path(os.path.join(settings.BASE_DIR, f'sde/sde/universe/eve/{region_name}'))
        with open(region_directory_path/'region.yaml', 'r') as file:
            data = yaml.safe_load(file)
            region_id = data['regionID']

        for region_item in region_directory_path.iterdir():
            if region_item.is_dir():
                print(f'constellation: {region_item.name}')
                with open(region_item/'constellation.yaml', 'r') as file:
                    data = yaml.safe_load(file)
                    constellation_id = data['constellationID']
                for constellation_item in region_item.iterdir():
                    if constellation_item.is_dir():
                        print(f'solar system: {constellation_item.name}')
                        with open(constellation_item/'solarsystem.yaml', 'r') as file:
                            data = yaml.safe_load(file)
                            solarsystem_id = data['solarSystemID']
                            solar_system = SolarSystem()
                            solar_system.system_id = solarsystem_id
                            solar_system.name = _pascal_case_to_spaces(constellation_item.name)
                            solar_system.constellation_id = constellation_id
                            solar_system.region_id = region_id
                            solar_system.security = data['security']
                            solar_system.security_class = data['securityClass']  
                            solar_systems.append(solar_system)

    SolarSystem.objects.bulk_create(solar_systems, 
        update_conflicts=True, 
        unique_fields=['system_id'], 
        update_fields=['name', 'constellation_id', 'region_id', 'security', 'security_class'])
    return redirect('sde_index')

def update_jumps_to_trade_hub(request):
    trade_hubs = TradeHub.objects.all()
    for trade_hub in trade_hubs:
        print(f'trade_hub: {trade_hub.name}, system_id: {trade_hub.system_id}')
        solar_systems = SolarSystem.objects.filter(region_id=trade_hub.region_id)
        for solar_system in solar_systems:
            # route = client.get_op('get_route_origin_destination', origin=solar_system.system_id, destination=trade_hub.system_id)
            route = esi.client.Routes.get_route_origin_destination(origin=solar_system.system_id, destination=trade_hub.system_id).results()
            jumps = len(route) - 1
            print(f'{solar_system.name}, jumps: {jumps}, route: {route}')
            solar_system.jumps_to_trade_hub = jumps
        SolarSystem.objects.bulk_update(solar_systems, fields=['jumps_to_trade_hub'])

    return redirect('sde_index')

def _pascal_case_to_spaces(s):
    # Insert a space before each uppercase letter (except the first one)
    s = re.sub(r'(?<!^)([A-Z])', r' \1', s)
    return s

def sync_trade_items_data(request):
    trade_items = TradeItem.objects.all()
    to_save = []
    for trade_item in trade_items:
        if not trade_item.name or not trade_item.group_id or not trade_item.market_group_id:
            sde_type_id = SdeTypeId.objects.get(type_id=trade_item.type_id)
            trade_item.name = sde_type_id.name
            trade_item.group_id = sde_type_id.group_id
            trade_item.market_group_id = sde_type_id.market_group_id
            to_save.append(trade_item)
    if(len(to_save) > 0):
        TradeItem.objects.bulk_update(to_save, fields=['name', 'group_id', 'market_group_id'])

    return redirect('sde_index')

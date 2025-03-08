from django.http import HttpResponse, JsonResponse, QueryDict
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

from django.db.models import Min, Max, Avg, Sum

from market.models import MarketOrder, MarketTransaction, MarketRegionStatus, TradeItem, TradeHub, MarketHistory
from sde.models import SdeTypeId, NpcCorporation

from .services import market_service

import statistics

import time
from datetime import datetime, timezone

from django.core import serializers


# REGION_ID_HEIMATAR = 10000030
# REGION_ID_DOMAIN = 10000043
REGION_ID_FORGE = 10000002
# REGION_ID_SINQLAISON = 10000032
# REGION_ID_METROPOLIS = 10000042





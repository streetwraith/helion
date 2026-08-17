from django.contrib import admin
from .models import (
    EsiFetchState, MarketTransaction, TradeItem, TradeHub, TrackedCharacter,
)
from .services import esi_scheduler

admin.site.register(MarketTransaction)
admin.site.register(TradeItem)
admin.site.register(TradeHub)
admin.site.register(TrackedCharacter)


@admin.action(description="Re-enable and reset error state")
def reenable(modeladmin, request, queryset):
    esi_scheduler.reenable(queryset)


@admin.register(EsiFetchState)
class EsiFetchStateAdmin(admin.ModelAdmin):
    list_display = ('character_name', 'feed', 'next_due', 'last_success',
                    'consecutive_errors', 'last_error_at', 'disabled_at')
    actions = [reenable]
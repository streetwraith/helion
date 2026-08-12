from django.contrib import admin
from .models import (
    EsiFetchState, MarketOrder, MarketTransaction, MarketRegionStatus, TradeItem, TradeHub,
    TrackedCharacter,
)

admin.site.register(MarketOrder)
admin.site.register(MarketTransaction)
admin.site.register(MarketRegionStatus)
admin.site.register(TradeItem)
admin.site.register(TradeHub)
admin.site.register(TrackedCharacter)


@admin.action(description="Re-enable and reset error state")
def reenable(modeladmin, request, queryset):
    queryset.update(disabled_at=None, disabled_reason=None, consecutive_errors=0,
                    last_error=None, last_error_at=None, next_due=None)


@admin.register(EsiFetchState)
class EsiFetchStateAdmin(admin.ModelAdmin):
    list_display = ('character_name', 'feed', 'next_due', 'last_success',
                    'consecutive_errors', 'last_error_at', 'disabled_at')
    actions = [reenable]
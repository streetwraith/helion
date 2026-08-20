"""Price alerts: one condition per row, evaluated against the best price in scope.

Best ask is the lowest sell price in the scope, best bid is the highest buy
price. Both are statements about the whole book, which is what makes `ask >= 10`
meaningful: it says the cheapest offer is now 10 or above.

The evaluation is edge-triggered. A crossing sets is_triggered, and only a
snapshot that puts the price back on the other side clears it. The two operators
are exact complements, so there is no gap at the boundary and no hysteresis rule
to invent.
"""
from django.utils import timezone

from evesde import services as sde_service
from market.models import PriceAlert
from marketdata.models import Order, OrdersHub, RegionStatus

# Rows the bar shows. The fragment renders every triggered alert and hides the
# rest, so a card still fires for one the bar has no room for.
BAR_VISIBLE_ROWS = 3


def best_price(alert):
    """The best price on the alert's side over its whole scope, as
    (price, region_id). (None, None) when the scope holds no order on that side.

    One lookup costs about 5 ms warm across all 25 regions and under 1 ms for a
    single region: market.orders carries a (type_id, region_id, is_buy_order,
    price) index and is partitioned per region.
    """
    is_buy = alert.side == PriceAlert.Side.BID
    if alert.hubs_only:
        rows = OrdersHub.objects.filter(is_in_trade_hub_range=True)
    else:
        rows = Order.objects.all()
    rows = rows.filter(type_id=alert.type_id, is_buy_order=is_buy)
    if alert.region_id is not None:
        rows = rows.filter(region_id=alert.region_id)
    best = rows.order_by('-price' if is_buy else 'price').values('price', 'region_id').first()
    if best is None:
        return None, None
    return best['price'], best['region_id']


def condition_holds(alert, price):
    """Whether the alert's comparison is true for this price.

    A missing price is not a crossing, whichever operator the alert carries.
    Reading `ask >= 10` as vacuously true over an empty book would put a trigger
    in the bar with no price and no region to print.
    """
    if price is None:
        return False
    if alert.operator == PriceAlert.Operator.GTE:
        return price >= alert.threshold
    return price < alert.threshold


def evaluate(alert):
    """Compare the alert against the current book and store the result.

    Returns True only when this call is the crossing, so the caller can log it.
    Re-running against an unchanged snapshot writes the same values and crosses
    nothing, which is why the beat task needs no cache mark.
    """
    price, region_id = best_price(alert)
    holds = condition_holds(alert, price)
    crossed = holds and not alert.is_triggered
    if crossed:
        alert.triggered_at = timezone.now()
    elif not holds:
        alert.triggered_at = None
    alert.is_triggered = holds
    alert.triggered_price = price if holds else None
    alert.triggered_region_id = region_id if holds else None
    alert.save(update_fields=['is_triggered', 'triggered_price',
                              'triggered_region_id', 'triggered_at'])
    return crossed


def evaluate_all():
    """Re-evaluate every alert. Returns the number that crossed on this run."""
    return sum(1 for alert in PriceAlert.objects.all() if evaluate(alert))


def bar_context():
    """The alert bar's rows, newest crossing first.

    Deliberately uncached, unlike the fetch-warning bar beside it: an empty
    board costs one indexed row count, and the poller refreshes this same
    fragment every minute, so a cached page render would disagree with it.
    """
    alerts = list(PriceAlert.objects.filter(is_triggered=True)
                  .order_by('-triggered_at', 'id'))
    if not alerts:
        return {'alert_rows': [], 'alert_hidden_count': 0}
    type_names = sde_service.get_type_names({alert.type_id for alert in alerts})
    region_names = dict(RegionStatus.objects.values_list('region_id', 'region_name'))
    rows = [
        {
            'alert_id': alert.id,
            'type_id': alert.type_id,
            'name': type_names.get(alert.type_id, str(alert.type_id)),
            'side': alert.side,
            'operator': alert.operator,
            'threshold': alert.threshold,
            'price': alert.triggered_price,
            'region_id': alert.triggered_region_id,
            'region_name': region_names.get(alert.triggered_region_id,
                                            str(alert.triggered_region_id)),
            # The poller's identity for one fire. It holds still while the
            # trigger stands, so navigating between pages never re-fires a card.
            'fired_at': alert.triggered_at.isoformat() if alert.triggered_at else '',
            'visible': index < BAR_VISIBLE_ROWS,
        }
        for index, alert in enumerate(alerts)
    ]
    return {'alert_rows': rows,
            'alert_hidden_count': max(0, len(rows) - BAR_VISIBLE_ROWS)}

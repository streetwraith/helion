"""Blueprint profitability: the cost of the materials against the value of the product.

Both sides are read at the daily `average`, so they face the same bid-ask spread
and it cancels. The chart therefore answers "convert these materials, or sell
them as they are", which is not the same question as "buy at the ask, build, sell
at the bid" - no historical order book exists to answer that one.
"""
import math

from market.industry_constants import (
    BASE_MATERIALS,
    MATERIAL_EFFICIENCY,
    OUTPUT_QUANTITY,
    PRODUCT_TYPE_ID,
)
# Shared with the item history chart on purpose: EVE's market day is a UTC day,
# and the two charts have to label the same day identically.
from market.services.history import _epoch_utc, get_market_history_bulk


def material_quantity(base_quantity, material_efficiency, runs=1):
    """EVE's material rule: reduce by ME, round to 2 decimals, then round up.

    The round to 2 decimals is EVE's own and is not cosmetic. It absorbs the
    float error that would otherwise turn 350 * 0.9 into 315.00000000000006 and
    consume a whole extra unit. A run always consumes at least one unit.
    """
    assert base_quantity > 0
    assert 0 <= material_efficiency <= 10
    assert runs > 0
    reduced = runs * base_quantity * (1 - material_efficiency / 100)
    return max(runs, math.ceil(round(reduced, 2)))


def recipe_quantities():
    """type_id -> units one run consumes at the blueprint's material efficiency."""
    return {type_id: material_quantity(base, MATERIAL_EFFICIENCY)
            for type_id, base in BASE_MATERIALS.items()}


def _carried_forward(records):
    """One price per day, where a day without a trade keeps the last price.

    Market history has no row for a day a type did not trade, so the gap-filled
    records carry None there. The stack has to stay continuous across such a
    day. Days before the type's first trade stay None: nothing to carry yet.
    """
    prices = []
    last = None
    for record in records:
        if record.average is not None:
            last = float(record.average)
        prices.append(last)
    return prices


def get_blueprint_chart(region_id, days):
    """The uPlot rows for the blueprint chart, or None without history.

    The rows are x, then one running total per material in BASE_MATERIALS order,
    then the material total again, the product revenue and the margin percent.
    The material rows are a running sum so uPlot can draw each material as the
    band between its own row and the row below it. The total repeats as its own
    row because it is also a line, and a band edge carries no stroke.

    A day on which a material has still never traded carries None through the
    running sum, the total and the margin, so the chart breaks there instead of
    drawing a total that is short one material.
    """
    assert days > 0
    quantities = recipe_quantities()
    type_ids = [*quantities, PRODUCT_TYPE_ID]
    # days_back counts back from the newest day and the window includes both ends.
    history = get_market_history_bulk(region_id, type_ids, days_back=days - 1)

    product_records = history[PRODUCT_TYPE_ID]
    if not any(record.average is not None for record in product_records):
        return None

    prices = {type_id: _carried_forward(history[type_id]) for type_id in type_ids}
    days_axis = [_epoch_utc(record.date) for record in product_records]

    running = [0.0] * len(days_axis)
    material_rows = []
    for type_id, quantity in quantities.items():
        running = [None if total is None or price is None else total + quantity * price
                   for total, price in zip(running, prices[type_id])]
        material_rows.append(list(running))

    total = material_rows[-1]
    revenue = [None if price is None else OUTPUT_QUANTITY * price
               for price in prices[PRODUCT_TYPE_ID]]
    margin = [None if cost is None or income is None else (income - cost) / cost * 100
              for cost, income in zip(total, revenue)]

    # A window longer than the ingested history leaves leading days on which
    # nothing has traded yet. Drop them rather than open the chart with a gap.
    first = next((index for index, value in enumerate(total) if value is not None), None)
    if first is None:
        return None
    rows = [days_axis, *material_rows, list(total), revenue, margin]
    return [row[first:] for row in rows]

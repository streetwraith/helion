"""The gas huffing calculator: what a gas site holds, and what a hub pays for it.

The arithmetic takes its prices as an argument, so it runs without an order book.
Only `gas_quotes` reads the database.
"""
from evesde.models import Type
from market.services.orders import best_orders_by_type, get_orders_in_hub_range

PRICE_BASES = ('bid', 'ask', 'mid')


class GasDataMissing(Exception):
    """A site names a type the sde does not carry, so its m3 cannot be known."""


def fleet_setup(boost_rate, frigate_rate, hold, residue_chance):
    """The panel above the table. One fleet has one harvest rate.

    The v3 spreadsheet divides the hold by the frigate rate while its clear
    times divide by the combined rate. The hold is the whole fleet's here, so
    all three figures agree.
    """
    assert boost_rate >= 0, 'boost rate must not be negative'
    assert frigate_rate >= 0, 'frigate rate must not be negative'
    assert hold > 0, 'hold must be above zero'
    assert residue_chance >= 0, 'residue chance must not be negative'
    harvest_rate = boost_rate + frigate_rate
    assert harvest_rate > 0, 'total harvest rate must be above zero'
    return {
        'harvest_rate': harvest_rate,
        'hourly_harvest': harvest_rate * 3600,
        'trip_minutes': hold / harvest_rate / 60,
        'hold': hold,
        # Residue destroys gas above what the ship banks, so the cloud pays out
        # less and empties sooner. Both follow from this one factor.
        'efficiency': 100 / (100 + residue_chance),
    }


def gas_quotes(region_id, basis, compressed_by_raw):
    """Per raw type id: the unit volume, and the ISK per m3 the hub pays.

    Both forms of a gas divide by the raw volume, because one raw unit
    compresses to one compressed unit. The better of the two wins. A gas with no
    order on the chosen side in either form gets None, never a zero.
    """
    if basis not in PRICE_BASES:
        raise ValueError(f'unknown price basis: {basis}')
    volumes = dict(Type.objects.filter(type_id__in=compressed_by_raw)
                   .values_list('type_id', 'volume'))
    quotes = _best_quotes(region_id,
                          list(compressed_by_raw) + list(compressed_by_raw.values()))
    priced = {}
    for raw_id, compressed_id in compressed_by_raw.items():
        volume = volumes.get(raw_id)
        if not volume:
            raise GasDataMissing(f'sde.types carries no volume for type {raw_id}')
        sides = [price for price in (_quote_price(quotes[raw_id], basis),
                                     _quote_price(quotes[compressed_id], basis))
                 if price is not None]
        priced[raw_id] = {
            'volume': volume,
            'isk_per_m3': max(sides) / volume if sides else None,
        }
    return priced


def _best_quotes(region_id, type_ids):
    """type id -> the highest bid and the lowest ask in one hub."""
    orders = get_orders_in_hub_range(type_ids).filter(region_id=region_id)
    bids = best_orders_by_type(orders, is_buy=True)
    asks = best_orders_by_type(orders, is_buy=False)
    return {type_id: {'bid': _price(bids.get(type_id)), 'ask': _price(asks.get(type_id))}
            for type_id in type_ids}


def _price(order):
    """An order's price as a float; the rest of the maths is float, and the
    column is numeric."""
    return None if order is None else float(order.price)


def _quote_price(quote, basis):
    """One side of a book, or None when that side is empty.

    A mid needs both sides: with one side missing it would report half a price.
    """
    if basis == 'mid':
        if quote['bid'] is None or quote['ask'] is None:
            return None
        return (quote['bid'] + quote['ask']) / 2
    return quote['bid'] if basis == 'bid' else quote['ask']


def site_rows(family, quotes, setup):
    """One row per site of the family, each carrying its own cloud rows."""
    return [_site_row(site, quotes, setup) for site in family.sites]


def _site_row(site, quotes, setup):
    clouds = [_cloud_row(cloud, quotes, setup) for cloud in site.clouds]
    total_m3 = sum(cloud['m3'] for cloud in clouds)
    assert total_m3 > 0, f'{site.name} holds no gas'
    minutes = total_m3 / setup['harvest_rate'] / 60
    values = [cloud['value'] for cloud in clouds]
    # One unpriced cloud leaves the whole site value unknown. Counting it as
    # zero would read as a real site that happens to be cheap.
    value = None if None in values else sum(values)
    return {
        'name': site.name,
        'group': site.group,
        'extra': site.extra,
        'clouds': clouds,
        'm3': total_m3,
        'minutes': minutes,
        # The exact ratio, not the spreadsheet's ROUNDUP: 0.9 and 2.1 say
        # "one hold nearly fills" and "two holds and a little", which a whole
        # number of trips hides.
        'trips': total_m3 / setup['hold'],
        'value': value,
        'isk_per_hour': None if value is None else value / (minutes / 60),
    }


def _cloud_row(cloud, quotes, setup):
    quote = quotes[cloud.type_id]
    m3 = cloud.units * quote['volume'] * setup['efficiency']
    isk_per_m3 = quote['isk_per_m3']
    return {
        'type_id': cloud.type_id,
        'label': cloud.label,
        'extra': cloud.extra,
        'units': cloud.units,
        'm3': m3,
        'isk_per_m3': isk_per_m3,
        'value': None if isk_per_m3 is None else m3 * isk_per_m3,
    }

from django.db.models import Case, IntegerField, Value, When

from evesde.models import Type

# A shorter query matches thousands of names and helps nobody.
MIN_SEARCH_LENGTH = 3
MAX_SEARCH_RESULTS = 20


def get_type_names(type_ids):
    type_names = Type.objects.filter(type_id__in=type_ids).values("type_id", "name")
    return {item["type_id"]: item["name"] for item in type_names}


def search_market_type_names(query, limit=MAX_SEARCH_RESULTS):
    """Tradeable types whose name contains `query`, prefix matches first.

    Only a type with a market group can appear on the market. `icontains`
    compiles to ILIKE '%q%', which no index serves - and the sde schema belongs
    to sdemanager, so helion cannot add one. The scan is over ~53k rows.
    """
    assert limit > 0
    query = query.strip()
    if len(query) < MIN_SEARCH_LENGTH:
        return []
    return list(
        Type.objects.filter(market_group_id__isnull=False, name__icontains=query)
        .annotate(prefix_rank=Case(
            When(name__istartswith=query, then=Value(0)),
            default=Value(1),
            output_field=IntegerField()))
        .order_by("prefix_rank", "name")
        .values("type_id", "name")[:limit]
    )

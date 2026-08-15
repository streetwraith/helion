from django.db import connection
from django.db.models import Case, IntegerField, Value, When

from evesde.models import MarketGroup, MetaGroup, Type

# A shorter query matches thousands of names and helps nobody.
MIN_SEARCH_LENGTH = 3
MAX_SEARCH_RESULTS = 20

# The deepest market group path is five levels; the bound stops a cycle in the
# upstream data from looping forever, since sde carries no foreign keys.
MAX_MARKET_GROUP_DEPTH = 10


def get_type_names(type_ids):
    type_names = Type.objects.filter(type_id__in=type_ids).values("type_id", "name")
    return {item["type_id"]: item["name"] for item in type_names}


def get_market_group_options(excluded_root_ids=()):
    """The market groups as a flat, ordered list for a select.

    Each entry carries its depth, so the caller can indent it. The order is
    depth-first and alphabetical, which is the order a person reads a tree in.

    Two shaping rules:

    - A group under an excluded root is dropped with the root.
    - A leaf is dropped when every one of its siblings is a leaf too. That is
      the terminal size or slot split - Small/Medium/Large/Capital Armor Rigs,
      Implant Slot 06 to 10 - which is finer than this filter needs. A leaf
      that sits beside a group with children stays, because it is a category
      in its own right: Afterburners keeps its 70 types beside Propulsion's
      other children.
    """
    groups = {
        row["market_group_id"]: {"parent": row["parent_group_id"], "name": row["name"], "kids": []}
        for row in MarketGroup.objects.values("market_group_id", "parent_group_id", "name")
    }
    roots = []
    for group_id, group in groups.items():
        siblings = groups[group["parent"]]["kids"] if group["parent"] in groups else roots
        siblings.append(group_id)
    for group in groups.values():
        group["kids"].sort(key=lambda group_id: groups[group_id]["name"].casefold())
    roots.sort(key=lambda group_id: groups[group_id]["name"].casefold())

    def is_leaf(group_id):
        return not groups[group_id]["kids"]

    def keep(group_id, parent_id):
        if not is_leaf(group_id) or parent_id is None:
            return True
        return not all(is_leaf(sibling) for sibling in groups[parent_id]["kids"])

    options = []

    def walk(group_id, depth):
        options.append({"market_group_id": group_id, "name": groups[group_id]["name"], "depth": depth})
        for kid in groups[group_id]["kids"]:
            if keep(kid, group_id):
                walk(kid, depth + 1)

    for root_id in roots:
        if root_id not in excluded_root_ids:
            walk(root_id, 0)
    return options


def get_meta_groups():
    """The meta groups as (id, name), lowest id first: the exclude filter's legend."""
    return list(MetaGroup.objects.order_by("meta_group_id").values_list("meta_group_id", "name"))


def get_market_type(type_id):
    """One type as a dict with its market group, or None when it does not resolve."""
    return Type.objects.filter(type_id=type_id).values(
        "type_id", "name", "market_group_id").first()


def get_market_group_path(market_group_id):
    """The market group names from the root down to this group.

    One recursive query rather than a walk of one query per level. An item with
    no market group, or an id that does not resolve, yields an empty path.
    """
    if market_group_id is None:
        return []
    query = """
    WITH RECURSIVE path AS (
        SELECT _key, parent_group_id, name_en, 0 AS depth
        FROM sde.market_groups WHERE _key = %s
        UNION ALL
        SELECT parent._key, parent.parent_group_id, parent.name_en, path.depth + 1
        FROM sde.market_groups AS parent
        JOIN path ON parent._key = path.parent_group_id
        WHERE path.depth < %s
    )
    SELECT name_en FROM path ORDER BY depth DESC
    """
    with connection.cursor() as cursor:
        cursor.execute(query, [market_group_id, MAX_MARKET_GROUP_DEPTH])
        return [row[0] for row in cursor.fetchall()]


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

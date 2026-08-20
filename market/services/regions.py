"""The ingested regions as dropdown options.

Two pages offer the same 25-entry region select - the history chart and the
price alerts - so the filing rule lives here rather than in either view.
"""
from marketdata.models import RegionStatus

# EVE names three of the ingested regions "The <something>". In a list of 25 the
# article buries them under one letter, so the dropdown moves it to the end and
# sorts on that: "The Forge" files under F. The trailing space matters - it keeps
# a name like "Thera" out of the rule.
ARTICLE = 'The'


def filed_region_name(name):
    """"The Forge" as "Forge, The". Any other name unchanged."""
    prefix = ARTICLE + ' '
    if name.startswith(prefix):
        return f'{name[len(prefix):]}, {ARTICLE}'
    return name


def region_names():
    """Every ingested region as {region_id: name}, in its natural form."""
    return dict(RegionStatus.objects.values_list('region_id', 'region_name'))


def region_options(names):
    """The dropdown pairs for those names, filed and sorted.

    Case-folded, because sorting on raw codepoints files every capital before
    every lower-case letter: "GPMR-01" would land ahead of "Genesis".
    """
    return sorted(
        ((region_id, filed_region_name(name)) for region_id, name in names.items()),
        key=lambda option: option[1].casefold())

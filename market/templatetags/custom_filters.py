from django import template
from django.utils import timezone
import urllib.parse

register = template.Library()

SECONDS_PER_DAY = 86400


def _dhms(total_seconds):
    """A duration as '<days>d hh:mm:ss'.

    One width per column, so the values compare down a table by eye. Django's
    timesince says "2 months, 1 week", which rounds away the part that decides
    whether an order is stale and cannot be compared at a glance.
    """
    days, rest = divmod(int(total_seconds), SECONDS_PER_DAY)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{days}d {hours:02d}:{minutes:02d}:{seconds:02d}'


@register.filter(name='since_dhms')
def since_dhms(value):
    """How long ago a moment was. A future one reads as zero, not as negative:
    only a clock disagreement puts an order's timestamp ahead of now."""
    if value is None:
        return ''
    return _dhms(max(0, (timezone.now() - value).total_seconds()))


@register.filter(name='until_dhms')
def until_dhms(value):
    """How long until a moment. Already past says so, because a snapshot holds
    orders that reached their expiry between two refreshes."""
    if value is None:
        return ''
    remaining = (value - timezone.now()).total_seconds()
    return _dhms(remaining) if remaining > 0 else 'expired'

@register.filter(name='round')
def round_filter(value, ndigits=None):
    return round(value, ndigits if ndigits is not None else 0)

# A browser collapses leading spaces inside an <option>, so a tree indents
# there with non-breaking spaces. The character needs no escaping.
OPTION_INDENT = '\u00a0' * 4

@register.filter(name='option_indent')
def option_indent(depth):
    """One level of <option> indentation per depth step."""
    return OPTION_INDENT * int(depth)

@register.filter(name='get_by_key')
def get_by_key(value, arg):
    """Retrieve a value from a dictionary or an attribute from an object."""
    if isinstance(value, dict):
        return value.get(arg, {})
    else:
        return getattr(value, arg, {})
    
# Under this price the decimals carry information: Tritanium at 3.94 ISK must not
# render as 4, and an undercut is often 0.01 ISK. Over it, two decimals are noise
# on every aggregate in the app, where a rounding of 0.5 ISK changes no decision.
ISK_DECIMALS_BELOW = 1000

@register.filter(name='isk_value')
def isk_value(value):
    if(value == 0 or value == None or isinstance(value, dict)):
        return 0
    if abs(value) < ISK_DECIMALS_BELOW:
        return "{:,.2f}".format(value)
    return "{:,.0f}".format(value)

@register.filter(name='isk_value_k')
def isk_value_k(value):
    if(value == 0 or value == None or isinstance(value, dict) or value == ''):
        return 0
    return "{:,.1f}k".format(value/1000)

@register.filter(name='isk_value_mil')
def isk_value_mil(value):
    if(value == 0 or value == None or isinstance(value, dict) or value == ''):
        return 0
    return "{:,.1f}m".format(value/1000000)

@register.filter(name='sp_value')
def sp_value(value):
    """Skill points in millions. Not isk_value_mil: these are not ISK, and the
    two would drift apart the moment one of them changes."""
    if not value:
        return "0"
    return "{:,.1f}m".format(value / 1000000)

@register.filter(name='m3_value')
def m3_value(value):
    """A volume in m3. Small volumes carry their decimals - a rocket is 0.005 m3
    and a stack of them is what fills a hold. Large ones do not."""
    if value is None:
        return "-"
    if abs(value) < 100:
        return "{:,.2f}".format(value)
    return "{:,.0f}".format(value)

@register.filter(name='stradd')
def stradd(arg1, arg2):
    """concatenate arg1 & arg2"""
    return str(arg1) + str(arg2)

@register.filter(name='get_object_by_attr')
def get_object_by_attr(value, arg):
    """
    Get an object from the list where a specific attribute matches a given value.
    'value' is the list, and 'arg' should be a string in the format 'attr:value'
    where 'attr' is the name of the attribute and 'value' is the value to search for.
    """
    if not value or not isinstance(arg, str):
        return None

    attr, search_value = arg.split(':', 1)

    for item in value:
        if getattr(item, attr, None) == search_value or getattr(item, attr, None) == int(search_value):
            return item
    return None

@register.filter(name='dict_to_query')
def dict_to_query(value):
    if not value:
        return ""
    return "&"+urllib.parse.urlencode(value)


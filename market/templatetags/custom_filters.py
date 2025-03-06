from django import template
import urllib.parse

register = template.Library()

@register.filter(name='round')
def round_filter(value, ndigits=None):
    return round(value, ndigits if ndigits is not None else 0)

@register.filter(name='get_by_key')
def get_by_key(value, arg):
    """Retrieve a value from a dictionary or an attribute from an object."""
    if isinstance(value, dict):
        return value.get(arg, {})
    else:
        return getattr(value, arg, {})
    
@register.filter(name='isk_value')
def isk_value(value):
    if(value == 0 or value == None or isinstance(value, dict)):
        return 0
    return "{:,.0f}".format(value)

@register.filter(name='isk_value_mil')
def isk_value_mil(value):
    if(value == 0 or value == None or isinstance(value, dict)):
        return 0
    return "{:,.1f}m".format(value/1000000)

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

@register.filter(name='wallet_stats')
def wallet_stats(obj, args):
    param1, param2, param3 = args.split(',')
    param2 = int(param2)
    param3 = int(param3)
    return obj.get_data_for_range(param1, param2, param3)
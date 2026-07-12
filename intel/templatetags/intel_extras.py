from django import template

register = template.Library()


@register.filter
def lookup(value, key):
    """Dict access by a variable key (templates can't do d[var] natively)."""
    if value is None:
        return None
    return value.get(str(key))

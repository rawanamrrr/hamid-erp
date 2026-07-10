"""Simple integer division/remainder filters for box/strip quantity breakdowns
(e.g. pharmacy: total strips -> boxes + leftover strips). Not built into Django."""
from django import template

register = template.Library()


@register.filter
def div(value, arg):
    """Integer (floor) division: {{ value|div:arg }}."""
    try:
        return int(float(value)) // int(float(arg))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0


@register.filter
def mod(value, arg):
    """Remainder: {{ value|mod:arg }}."""
    try:
        return int(float(value)) % int(float(arg))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0

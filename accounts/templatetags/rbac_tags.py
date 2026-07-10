from django import template
from accounts.permissions import has_permission

register = template.Library()

@register.filter
def can_access(user, permission_string):
    """
    Usage in templates:
    {% if request.user|can_access:"users:view" %}
        ...
    {% endif %}
    """
    try:
        module, action = permission_string.split(':')
        return has_permission(user, module, action)
    except ValueError:
        return False


@register.filter
def get_item(dictionary, key):
    """Usage: {{ my_dict|get_item:key }}"""
    if isinstance(dictionary, dict):
        return dictionary.get(key, 0)
    return 0

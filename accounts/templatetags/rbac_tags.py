from django import template
from accounts.permissions import has_permission, has_granular_action, has_granular_action_open

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
def can_do(user, spec):
    """Granular menu gate WITH a fallback: "module:action|fallback_module:fallback_action".

    can_access has no fallback, so gating a menu entry on a fine-grained key hid it from
    every user whose role could not grant that key -- and roles cannot grant most of them,
    because they only exist as per-user overrides. This mirrors require_granular_action:
    the fine-grained key wins when present, an explicit per-user entry for the module is
    authoritative, and otherwise the permission that guarded the page before applies.

    Usage: {% if request.user|can_do:"products:list|products:view" %}
    """
    try:
        primary, fallback = spec.split('|')
        module, action = primary.split(':')
        fb_module, fb_action = fallback.split(':')
    except ValueError:
        return False
    return has_granular_action(user, module, action, fb_module, fb_action)


@register.filter
def can_do_open(user, spec):
    """Granular menu gate for an "open" action (require_granular_action_open) — default
    ALLOW unless the module has been explicitly customized for this user and this
    action left unchecked. Unlike can_do, there is no separate fallback permission
    (open actions have none): "module:action" only.

    Usage: {% if request.user|can_do_open:"sales:orders" %}
    """
    try:
        module, action = spec.split(':')
    except ValueError:
        return False
    return has_granular_action_open(user, module, action)


@register.filter
def get_item(dictionary, key):
    """Usage: {{ my_dict|get_item:key }}"""
    if isinstance(dictionary, dict):
        return dictionary.get(key, 0)
    return 0

from functools import wraps
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
import json

def has_permission(user, module, action):
    """
    Checks if a user has a specific permission.
    Master Account has HIGHEST PRIORITY and FULL ACCESS.
    """
    if not user.is_authenticated:
        return False
    try:
        if user.profile.is_master:
            return True
    except AttributeError:
        pass

    if user.is_superuser:
        return True
    
    # Staff users (is_staff) who are not superusers might still need role checks,
    # but often they are managers. In this system, we rely on the Role model.
        
    try:
        profile = user.profile
    except AttributeError:
        # Failsafe if profile is missing
        return False

    perms = profile.get_all_permissions()
    
    # Check if the action exists in the module's allowed actions
    if module in perms and action in perms[module]:
        return True
        
    # Also allow simple 'all' permission for full access to a module
    if module in perms and 'all' in perms[module]:
        return True
        
    return False

def get_best_landing_url(user):
    """
    Determines the best landing page URL for a user based on their permissions.
    """
    if not user.is_authenticated:
        return reverse('login')
        
    if has_permission(user, 'dashboard', 'view'):
        return reverse('dashboard')
    elif has_permission(user, 'pos', 'view'):
        return reverse('pos_view')

    # Neither dashboard nor POS is granted — fall back to the first page the
    # user's permissions actually unlock (same registry the navbar/favorites use).
    from .shortcuts import available_for
    available = available_for(user)
    if available:
        return available[0]['url']

    # Truly no permissions granted: show a friendly message instead of a 403 crash.
    return reverse('no_access')

def can_view_profit(user):
    """Resolved per-user profit-visibility flag (Phase 3.2).

    Master/superuser always pass (UserProfile.allows_view_profit handles that).
    Profile-less or anonymous users default to False (profit hidden).
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    try:
        return user.profile.allows_view_profit()
    except AttributeError:
        return bool(getattr(user, 'is_superuser', False))


def require_profit_view(view_func):
    """Gate whole profit-report pages behind the per-user can_view_profit flag.

    Bounces unauthorized users back to the dashboard with a message instead of a hard 403,
    since lacking profit visibility is an ordinary cashier state, not an error.
    """
    from django.contrib import messages

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if can_view_profit(request.user):
            return view_func(request, *args, **kwargs)
        messages.error(request, 'ليس لديك صلاحية الاطلاع على بيانات الأرباح.')
        return redirect('dashboard')
    return _wrapped_view


def has_granular_action(user, module, action, fallback_module, fallback_action):
    """Check a fine-grained action (e.g. financial:withdraw, pos:transfer) with a
    backward-compatible fallback.

    These granular actions only exist as explicit per-user overrides set via the
    sidebar-permissions tool (accounts.user_sidebar_permissions) — they are not
    part of any Role's checkbox set. So if an admin never customized `module` for
    this user, the granular action won't be in perms[module] and we must NOT treat
    that as a denial (that would silently break access for every user who predates
    this feature). Instead, fall back to whatever permission already gated the
    operation before granular actions existed. Only once `module` has an explicit
    direct_permissions entry for this user does the fine-grained action list
    become authoritative.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    try:
        if user.profile.is_master:
            return True
    except AttributeError:
        pass
    if getattr(user, 'is_superuser', False):
        return True

    if has_permission(user, module, action):
        return True

    try:
        dp = user.profile.direct_permissions or {}
    except AttributeError:
        dp = {}
    if module in dp:
        # Admin explicitly customized this module for this user via the sidebar
        # tool — the granular list is authoritative, no fallback.
        return False

    return has_permission(user, fallback_module, fallback_action)


def require_granular_action(module, action, fallback_module, fallback_action):
    """Like require_permission, but for fine-grained actions — see has_granular_action."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if has_granular_action(request.user, module, action, fallback_module, fallback_action):
                return view_func(request, *args, **kwargs)
            is_ajax = (
                request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or request.content_type == 'application/json'
                or '/api/' in request.path
            )
            if is_ajax:
                from django.http import JsonResponse
                return JsonResponse(
                    {'status': 'error', 'message': 'لا تمتلك صلاحية كافية للقيام بهذا الإجراء.'},
                    status=403,
                )
            raise PermissionDenied("You do not have permission to perform this action.")
        return _wrapped_view
    return decorator


def dashboard_widget_visible(user, key):
    """Whether a specific dashboard widget (e.g. 'items_sold', 'revenue') should be
    shown to this user. Defaults to visible (current/historical behavior) unless an
    admin has explicitly customized the 'dashboard' module for this user via the
    sidebar-permissions tool, in which case only the checked widgets show.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    try:
        if user.profile.is_master:
            return True
    except AttributeError:
        pass
    if getattr(user, 'is_superuser', False):
        return True
    try:
        dp = user.profile.direct_permissions or {}
    except AttributeError:
        dp = {}
    if 'dashboard' not in dp:
        return True
    allowed = dp.get('dashboard') or []
    return key in allowed or 'all' in allowed


def has_granular_action_open(user, module, action):
    """Like has_granular_action, but for views that previously had NO permission
    check at all (any logged-in user could reach them). There is no existing
    coarse permission to fall back to, so the fallback is simply "allowed" —
    identical to dashboard_widget_visible's default-allow behavior. Only once an
    admin explicitly customizes `module` for this user via the sidebar tool does
    the granular action list become authoritative.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    try:
        if user.profile.is_master:
            return True
    except AttributeError:
        pass
    if getattr(user, 'is_superuser', False):
        return True

    if has_permission(user, module, action):
        return True

    try:
        dp = user.profile.direct_permissions or {}
    except AttributeError:
        dp = {}
    if module in dp:
        return False

    return True


def require_granular_action_open(module, action):
    """Decorator counterpart to has_granular_action_open."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if has_granular_action_open(request.user, module, action):
                return view_func(request, *args, **kwargs)
            is_ajax = (
                request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or request.content_type == 'application/json'
                or '/api/' in request.path
            )
            if is_ajax:
                from django.http import JsonResponse
                return JsonResponse(
                    {'status': 'error', 'message': 'لا تمتلك صلاحية كافية للقيام بهذا الإجراء.'},
                    status=403,
                )
            raise PermissionDenied("You do not have permission to perform this action.")
        return _wrapped_view
    return decorator


def _cashier_shift_eligible(user, action, policy_key):
    """Shared logic for cashier_can_open_shift / cashier_can_close_shift.

    Anyone who can actually create POS sales (pos:create — the functional definition
    of "a cashier", regardless of what their Role is named) should be able to open and
    close shifts, since that's an operational necessity, not a privileged action.

    The one case that previously broke this: an admin uses the sidebar-permissions
    tool to hide the whole "الخزنة والشيفتات" section for a cashier (a blanket
    `direct_permissions['financial'] = ['__denied__']`, usually meant just to declutter
    their sidebar) — which also silently blocked shift management with no way to tell
    it apart from a deliberate exclusion. We now distinguish the two:
      - A blanket whole-module deny (`['__denied__']`) is treated as "never
        customized for shift purposes" — falls back to the pos:create check below.
      - An explicit granular list (admin picked specific financial sub-actions via
        the sidebar tool, e.g. ['view', 'withdraw']) is still respected as
        authoritative — if the admin deliberately left open_shift/close_shift off
        that list, that exclusion holds.
    """
    try:
        if user.profile.is_master:
            return True
    except AttributeError:
        pass
    if getattr(user, 'is_superuser', False):
        return True

    from settings.policies import get_policy
    if not get_policy(policy_key):
        return False

    if has_permission(user, 'financial', action):
        return True

    try:
        dp = user.profile.direct_permissions or {}
    except AttributeError:
        dp = {}
    financial_dp = dp.get('financial')
    if financial_dp is not None and financial_dp != ['__denied__']:
        # Admin deliberately picked specific financial actions for this user —
        # respect that list strictly, no fallback.
        return False

    # Not customized (or only blanket-hidden) — anyone who can make POS sales is,
    # functionally, a cashier and needs to be able to open/close their own shift.
    return has_permission(user, 'pos', 'create')


def cashier_can_open_shift(user):
    """Whether this user should see/use the open-shift option (POS popup + dashboard
    button + the start_shift/start_shift_ajax endpoints). See _cashier_shift_eligible."""
    return _cashier_shift_eligible(user, 'open_shift', 'shifts.cashier_can_open_shift')


def cashier_can_close_shift(user):
    """Whether this user should see/use the close-shift option (POS button +
    dashboard button + the close_shift endpoint). See _cashier_shift_eligible."""
    return _cashier_shift_eligible(user, 'close_shift', 'shifts.cashier_can_close_shift')


def require_permission(module, action):
    """
    Decorator for views to enforce RBAC permissions.
    Usage: @require_permission('products', 'edit')
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if has_permission(request.user, module, action):
                return view_func(request, *args, **kwargs)
            else:
                # AJAX/API calls expect JSON back — raising PermissionDenied here renders
                # Django's HTML 403 page instead, which the caller's fetch(...).then(r =>
                # r.json()) then fails to parse ("Unexpected token '<'"), so the denial is
                # never actually shown to the user — it just looks like nothing happened.
                is_ajax = (
                    request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                    or request.content_type == 'application/json'
                    or request.path.startswith('/sales/api/')
                    or '/api/' in request.path
                )
                if is_ajax:
                    from django.http import JsonResponse
                    return JsonResponse(
                        {'status': 'error', 'message': 'لا تمتلك صلاحية كافية للقيام بهذا الإجراء.'},
                        status=403,
                    )
                # Can raise PermissionDenied to render a custom 403 page
                raise PermissionDenied("You do not have permission to perform this action.")
        return _wrapped_view
    return decorator

"""Manager-approval workflow (Phase 3.3).

Turns the hard per-user/store guards at checkout into "reject OR escalate". A privileged
user authorizes an override inline (credentials carried in the checkout payload); each
override is persisted as an ApprovalRequest for audit. Kept deliberately small: the
checkout view collects `violations` (dicts with `kind`/`message`/`detail`), then calls
`authorize_inline()` and, on success, `record_approvals()`.
"""
from django.contrib.auth import authenticate
from django.utils import timezone

from .permissions import has_permission

# Override kinds — mirror ApprovalRequest.KIND_CHOICES and the checkout guards.
OVER_DISCOUNT = 'over_discount'
BELOW_COST = 'below_cost'
BELOW_SALE_PRICE = 'below_sale_price'
CHANGE_UNIT = 'change_unit'
BELOW_ZERO_STOCK = 'below_zero_stock'
OVER_CREDIT_LIMIT = 'over_credit_limit'


def is_authorizer(user):
    """Who may approve overrides: superuser, master, or anyone with sales:manage."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    prof = getattr(user, 'profile', None)
    if prof is not None and getattr(prof, 'is_master', False):
        return True
    return has_permission(user, 'sales', 'manage')


def authorize_inline(username, password, requester):
    """Authenticate an approver by credentials and confirm they may authorize overrides.

    Returns the approver `User` on success, else None. A user cannot approve their own
    override, and the approver must be active and an authorizer.
    """
    if not username or not password:
        return None
    user = authenticate(username=username, password=password)
    if user is None or not user.is_active:
        return None
    if requester is not None and user.pk == getattr(requester, 'pk', None):
        return None  # no self-approval
    if not is_authorizer(user):
        return None
    return user


def describe_authorize_failure(username, password, requester):
    """Why `authorize_inline` returned None, for a clearer inline error message.

    Doesn't change the pass/fail decision (that's still authorize_inline's job) — this
    just re-derives *which* check failed so the UI isn't stuck saying "بيانات خاطئة"
    for a case like self-approval, where the credentials themselves were actually
    correct. Only called after authorize_inline already returned None.
    """
    if not username or not password:
        return 'missing'
    user = authenticate(username=username, password=password)
    if user is None or not user.is_active:
        return 'bad_credentials'
    if requester is not None and user.pk == getattr(requester, 'pk', None):
        return 'self_approval'
    if not is_authorizer(user):
        return 'not_authorizer'
    return None  # shouldn't happen if authorize_inline already failed


def record_approvals(requester, approver, violations):
    """Persist one approved ApprovalRequest per violation. Returns the created rows."""
    from .models import ApprovalRequest
    now = timezone.now()
    rows = []
    for v in violations:
        rows.append(ApprovalRequest(
            requested_by=requester,
            approved_by=approver,
            kind=v.get('kind', ''),
            status='approved',
            payload=v.get('detail', {}) or {},
            note=v.get('message', '') or '',
            decided_at=now,
        ))
    if rows:
        ApprovalRequest.objects.bulk_create(rows)
    return rows

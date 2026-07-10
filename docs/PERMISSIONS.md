# Permission Matrix (Phase 3.1)

RBAC is enforced via `@require_permission(module, action)` (see `accounts/permissions.py`).
Master accounts (`profile.is_master`) and Django superusers bypass all checks.

Permission data lives on **Role.permissions** (JSON) and **UserProfile.direct_permissions**,
merged by `UserProfile.get_all_permissions()`. A module key with action `all` grants every
action in that module.

## Modules & actions now enforced

| Module | Actions | Applied to |
|--------|---------|-----------|
| `pos` | view, create | POS screens, checkout, shift open/close/print |
| `sales` | edit, delete, refund | edit invoice, void invoice, refund — **managers only** |
| `financial` | view, manage | view = dashboards/accounts/transactions/statements; manage = create accounts, withdrawals, add transactions, salaries, deals |
| `crm` | view, create, edit, delete | customer screens; pay-debt requires `edit`; delete requires `delete` |
| `shipping` | view, edit | shipping dashboard/labels = view; create/update/delete = edit |
| `products` | view, add, edit | (pre-existing) product/inventory management |
| `dashboard` | view | (pre-existing) |
| `users` | view, create, edit, delete | (pre-existing) account management |
| `settings` | view | (pre-existing) |

## Design rules

- **Destructive / money-moving actions** (`sales.delete`, `sales.refund`, `sales.edit`,
  `financial.manage`) are intentionally NOT granted to any current cashier role, so only
  owners (superusers/master) can perform them. Create a manager Role with these actions to
  delegate without giving superuser.
- **Cashiers keep**: `pos.view`, `pos.create` (sell + shift). They lost the ability to
  delete invoices, issue refunds, and view profit — which they previously had implicitly
  because those views were unprotected.
- **Server-side warehouse restriction**: checkout rejects a warehouse the cashier is not
  assigned to (`UserProfile.allowed_warehouses`), not just hidden in the dropdown.

## Suggested manager role JSON

```json
{
  "pos": ["view", "create"],
  "sales": ["edit", "delete", "refund"],
  "financial": ["view", "manage"],
  "crm": ["view", "create", "edit", "delete"],
  "shipping": ["view", "edit"],
  "products": ["view", "add", "edit"],
  "dashboard": ["view"]
}
```

## Follow-ups (later phases)

- Remove `@csrf_exempt` from POS AJAX endpoints once the frontend sends the CSRF token
  (kept for now to avoid breaking the live POS without browser testing).
- Per-user operational flags (max discount %, sell below cost, backdate) — Phase 3.2.
- Approval workflow for refunds/voids above a threshold — Phase 3.4.

"""MarketProfile engine (Phase ③) — single source of truth for market-type behavior.

Each market type maps to a *profile*: terminology overrides, which product fields/sections
are exposed, default unit, image-search prefix, and the inventory feature flags that market
cares about. Templates/forms/views read the active profile via the `market_profile` context
processor (exposed as `mp`) instead of scattered `{% if market_type == 'x' %}` chains.

Design rules (per product owner):
- `general` is a BARE BASE — neutral terminology, NO market-specific fields/sections. It is
  literally `_BASE` with nothing turned on.
- Every other market is GENUINELY UNIQUE — it turns on its own distinct fields/terms.
- This registry is the ONLY place market types are defined. `MARKET_TYPE_CHOICES`
  (settings + licensing) derive from it, so there is no drift.
- Switching market type only re-labels / shows-hides — it never corrupts stored data.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Bare baseline (the `general` profile). Every market starts from this and overrides.
# ─────────────────────────────────────────────────────────────────────────────
_BASE = {
    'key': 'general',
    'label': 'متجر عام',
    'icon': 'fa-store',
    # Terminology shown across product form / POS / receipts.
    'terms': {
        'cost_label':      'سعر التكلفة',
        'retail_label':    'سعر البيع',
        'wholesale_label': 'نص جملة',
        'unit_label':      'وحدة القياس',
        'pieces_label':    'قطع في العبوة',
        'pieces_hint':     'عدد القطع في العبوة',
        'specs_label':     'عام',
    },
    # Which optional product fields / form sections are exposed for this market.
    'show': {
        'sizes':             False,  # clothes size chips
        'clothes_specs':     False,  # fabric/textile spec block
        'pharmacy_specs':    False,  # scientific name / expiry / strips
        'electronics_specs': False,  # serials / warranty
        'grocery_specs':     False,  # weight / variants
        'pieces_per_package': False, # شيكارة / كرتون block
        'wholesale_price':   True,   # the نص جملة price field
        'weighted':          False,  # sold by weight (scale)
    },
    'default_unit': 'PCS',
    'image_prefix': '',
    # Inventory capabilities this market naturally uses (ties to 5.x features).
    'features': [],
}


def _profile(**overrides):
    """Build a profile from the bare base, deep-merging the `terms` and `show` dicts so a
    market only states what it changes."""
    import copy
    p = copy.deepcopy(_BASE)
    for k, v in overrides.items():
        if k in ('terms', 'show') and isinstance(v, dict):
            p[k].update(v)
        else:
            p[k] = v
    return p


MARKET_PROFILES = {
    'general': _profile(),  # bare base — nothing market-specific

    'clothes': _profile(
        key='clothes', label='ملابس وأقمشة', icon='fa-shirt',
        terms={
            'retail_label':  'قطاعي',
            'pieces_label':  'قطع في الشيكارة',
            'pieces_hint':   'للعرض بالشيكارة في المخازن',
            'specs_label':   'للنسيج والملابس',
        },
        show={'sizes': True, 'clothes_specs': True, 'pieces_per_package': True},
        image_prefix='ملابس ',
        features=['variants', 'sizes'],
    ),

    'pharmacy': _profile(
        key='pharmacy', label='صيدلية', icon='fa-prescription-bottle-medical',
        terms={
            'cost_label':    'سعر الشراء',
            'retail_label':  'سعر البيع',
            'unit_label':    'وحدة البيع',
            'pieces_label':  'كمية في الكرتون',
            'pieces_hint':   'عدد الوحدات في الكرتون',
            'specs_label':   'للصيدلية',
        },
        show={'pharmacy_specs': True, 'pieces_per_package': True, 'wholesale_price': False},
        default_unit='PCS',
        image_prefix='دواء ',
        features=['expiry', 'fefo', 'strips', 'batch'],
    ),

    'electronics': _profile(
        key='electronics', label='إلكترونيات', icon='fa-laptop',
        terms={'specs_label': 'للإلكترونيات'},
        show={'electronics_specs': True},
        image_prefix='جهاز ',
        features=['serials', 'warranty'],
    ),

    'grocery': _profile(
        key='grocery', label='بقالة وسوبرماركت', icon='fa-basket-shopping',
        terms={'unit_label': 'وحدة القياس/البيع', 'specs_label': 'للبقالة'},
        show={'grocery_specs': True, 'weighted': True},
        image_prefix='منتج ',
        features=['weighted', 'barcode', 'expiry'],
    ),

    'cafe': _profile(
        key='cafe', label='كافيه ومطعم', icon='fa-mug-saucer',
        terms={
            'cost_label':    'تكلفة التحضير',
            'retail_label':  'سعر البيع',
            'unit_label':    'وحدة البيع',
            'pieces_label':  'كود الصنف',
            'pieces_hint':   'كود الصنف داخل نطاق القسم',
            'specs_label':   'للكافيه/المطعم',
        },
        show={'wholesale_price': False},
        default_unit='PCS',
        image_prefix='مشروب أو طبق ',
        features=['tables', 'kds', 'modifiers', 'recipes', 'delivery', 'cash_custody'],
    ),
}

# Order shown in pickers / onboarding.
MARKET_ORDER = ['cafe', 'clothes', 'pharmacy', 'electronics', 'grocery', 'general']

# Derived choice list — the ONE place (settings + licensing import this).
MARKET_TYPE_CHOICES = [(k, MARKET_PROFILES[k]['label']) for k in MARKET_ORDER]

DEFAULT_MARKET = 'general'


def get_market_profile(market_type):
    """Return the profile dict for `market_type`, falling back to the bare base."""
    return MARKET_PROFILES.get(market_type or DEFAULT_MARKET, MARKET_PROFILES['general'])

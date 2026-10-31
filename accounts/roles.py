"""Single place that answers: what is this user allowed to be / see / do?"""
from django.conf import settings

MASTER, ADMIN, OPERATOR, CASHIER = 'master', 'admin', 'operator', 'cashier'
ROLE_LABELS = {MASTER: 'Master Admin', ADMIN: 'Admin', OPERATOR: 'Dispatch', CASHIER: 'Cashier'}

# The three price lists in your inventory form. They map 1:1 to the batch / product fields:
#   buying    -> purchase_price   ("Buying Price")
#   wholesale -> wholesale_price  ("Wholesale Price")
#   reselling -> selling_price    ("Reselling Price")
TIERS = [('buying', 'Buying'), ('wholesale', 'Wholesale'), ('reselling', 'Reselling')]
TIER_KEYS = [k for k, _ in TIERS]
TIER_LABELS = dict(TIERS)
# names used by the first version of the counter
LEGACY_TIERS = {'retail': 'reselling', 'reseller': 'reselling'}

DEFAULT_CASHIER_DISCOUNT = getattr(settings, 'POS_MAX_CASHIER_DISCOUNT', 10)

# What a Dispatch user may open. Everything else in the admin app is for Admin only.
# (url names from sku_manager/urls.py - edit this list if you add scan/return pages)
OPERATOR_URL_NAMES = {
    'sku_scanner', 'scan_dispatch',
    'pdf_order_scan', 'pdf_order_history', 'pdf_order_export', 'pdf_order_batch',
    'pdf_order_scan_file', 'pdf_order_stamped_file', 'pdf_order_review',
    'order_cancellation',
}


def get_profile(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    try:
        return user.profile
    except Exception:
        return None


def get_role(user):
    """master | admin | operator | cashier | None (anonymous).

    - a superuser is always master (so you can never lock yourself out)
    - a login with no profile yet is treated as admin, so your existing users keep working
    """
    if not getattr(user, 'is_authenticated', False):
        return None
    if user.is_superuser:
        return MASTER
    profile = get_profile(user)
    return profile.role if profile else ADMIN


def normalize_tier(key):
    key = LEGACY_TIERS.get(key, key)
    return key if key in TIER_KEYS else None


def parse_tiers(text):
    out = []
    for raw in (text or '').replace(' ', '').split(','):
        k = normalize_tier(raw)
        if k and k not in out:
            out.append(k)
    return out


def allowed_tiers(user):
    """Price lists the person may sell at. Only cashiers use the counter."""
    if get_role(user) != CASHIER:
        return []
    profile = get_profile(user)
    return (parse_tiers(profile.price_tiers) if profile else []) or ['reselling']


def default_tier(user):
    tiers = allowed_tiers(user)
    profile = get_profile(user)
    wanted = normalize_tier(profile.default_tier) if profile else None
    return wanted if wanted in tiers else (tiers[0] if tiers else 'reselling')


def max_discount(user):
    profile = get_profile(user)
    if profile and profile.max_discount is not None:
        return min(100, profile.max_discount)
    return DEFAULT_CASHIER_DISCOUNT


def can_void(user):
    profile = get_profile(user)
    return bool(get_role(user) == CASHIER and profile and profile.can_void)


def home_name(user):
    """URL name of the landing page for this user's role."""
    return {MASTER: 'master_home', ADMIN: getattr(settings, 'ADMIN_HOME_URL_NAME', 'dashboard'),
            OPERATOR: 'operator_home', CASHIER: 'pos'}.get(get_role(user), 'login')

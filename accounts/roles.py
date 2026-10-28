"""Single place that answers: what is this user allowed to be / see / do?"""
from django.conf import settings

MASTER, ADMIN, CASHIER = 'master', 'admin', 'cashier'
ROLE_LABELS = {MASTER: 'Master Admin', ADMIN: 'Admin', CASHIER: 'Cashier'}

# price lists a cashier can be given. "buying" = the cost price of the product.
TIERS = [('retail', 'Retail'), ('reseller', 'Reseller'), ('wholesale', 'Wholesale'), ('buying', 'Buying / cost')]
TIER_KEYS = [k for k, _ in TIERS]
TIER_LABELS = dict(TIERS)

DEFAULT_CASHIER_DISCOUNT = getattr(settings, 'POS_MAX_CASHIER_DISCOUNT', 10)


def get_profile(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    try:
        return user.profile
    except Exception:
        return None


def get_role(user):
    """master | admin | cashier | None (anonymous).

    - a superuser is always master (so you can never lock yourself out)
    - a login with no profile yet is treated as admin, so your existing users keep working
    """
    if not getattr(user, 'is_authenticated', False):
        return None
    if user.is_superuser:
        return MASTER
    profile = get_profile(user)
    return profile.role if profile else ADMIN


def is_manager(user):
    return get_role(user) in (MASTER, ADMIN)


def parse_tiers(text):
    return [t for t in (text or '').replace(' ', '').split(',') if t in TIER_KEYS]


def allowed_tiers(user):
    role = get_role(user)
    if role in (MASTER, ADMIN):
        return list(TIER_KEYS)
    if role == CASHIER:
        profile = get_profile(user)
        return parse_tiers(profile.price_tiers) or ['retail'] if profile else ['retail']
    return []


def default_tier(user):
    tiers = allowed_tiers(user)
    profile = get_profile(user)
    wanted = profile.default_tier if profile else 'retail'
    return wanted if wanted in tiers else (tiers[0] if tiers else 'retail')


def max_discount(user):
    role = get_role(user)
    if role in (MASTER, ADMIN):
        return 100
    profile = get_profile(user)
    if profile and profile.max_discount is not None:
        return min(100, profile.max_discount)
    return DEFAULT_CASHIER_DISCOUNT


def home_name(user):
    """URL name of the landing page for this user's role."""
    return {MASTER: 'master_home', ADMIN: getattr(settings, 'ADMIN_HOME_URL_NAME', 'dashboard'),
            CASHIER: 'pos'}.get(get_role(user), 'login')

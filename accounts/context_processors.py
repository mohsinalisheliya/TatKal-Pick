from .roles import ROLE_LABELS, get_role


def role(request):
    r = get_role(getattr(request, 'user', None))
    return {'role': r, 'role_label': ROLE_LABELS.get(r, ''), 'is_master': r == 'master',
            'is_manager': r in ('master', 'admin'), 'is_cashier': r == 'cashier'}

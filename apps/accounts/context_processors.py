from .roles import ROLE_LABELS, can_void, get_role


def role(request):
    user = getattr(request, 'user', None)
    r = get_role(user)
    return {'role': r, 'role_label': ROLE_LABELS.get(r, ''), 'is_master': r == 'master', 'is_admin_role': r == 'admin',
            'is_operator': r == 'operator', 'is_cashier': r == 'cashier', 'can_void': can_void(user)}

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from .roles import get_role, home_name


def role_required(*roles):
    """Allow only the listed roles; everybody else is sent to the page that belongs to them."""
    def deco(view):
        @wraps(view)
        def wrapper(request, *a, **kw):
            if not request.user.is_authenticated:
                return redirect(f'/login/?next={request.path}')
            if get_role(request.user) not in roles:
                messages.error(request, 'You do not have access to that page.', fail_silently=True)
                return redirect(home_name(request.user))
            return view(request, *a, **kw)
        return wrapper
    return deco

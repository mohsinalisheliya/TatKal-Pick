from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from .audit import log
from .roles import ADMIN, CASHIER, MASTER, get_role, home_name

SKIP_LOG_PREFIXES = ('/static/', '/media/', '/favicon')


class RoleAccessMiddleware:
    """The door lock. Put it after AuthenticationMiddleware and MessageMiddleware.

    cashier -> only /pos/ (+ login, logout, change password, static, media)
    admin   -> everything except /master/
    master  -> everything
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _common(self):
        return [reverse('login'), reverse('logout'), reverse('password_change'),
                getattr(settings, 'STATIC_URL', '/static/') or '/static/',
                getattr(settings, 'MEDIA_URL', '/media/') or '/media/']

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            role, path = get_role(user), request.path
            if not any(path.startswith(p) for p in self._common()):
                if role == CASHIER and not path.startswith(reverse('pos')):
                    return redirect('pos')
                if role == ADMIN and path.startswith(reverse('master_home')):
                    messages.error(request, 'Only the Master Admin can open that page.', fail_silently=True)
                    return redirect(home_name(user))
        return self.get_response(request)


class AuditMiddleware:
    """Records every change request (POST/PUT/PATCH/DELETE) and file downloads of signed-in users.
    Views that write their own, richer entry (sales, user changes) are not logged twice."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            if (request.user.is_authenticated and not getattr(request, '_audited', False)
                    and not request.path.startswith(SKIP_LOG_PREFIXES)):
                is_write = request.method in ('POST', 'PUT', 'PATCH', 'DELETE')
                is_download = request.method == 'GET' and (
                    'attachment' in response.get('Content-Disposition', '') or 'export' in request.path)
                if is_write or is_download:
                    log(request, 'download' if is_download else 'change', f'{request.method} {request.path}',
                        status=response.status_code)
        except Exception:
            pass
        return response

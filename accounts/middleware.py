from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse

from .audit import log
from .roles import ADMIN, CASHIER, MASTER, OPERATOR, OPERATOR_URL_NAMES, get_role, home_name

SKIP_LOG_PREFIXES = ('/static/', '/media/', '/favicon')


class RoleAccessMiddleware:
    """The door lock. Put it after AuthenticationMiddleware and MessageMiddleware.

    Every role lives in its own app and cannot open the others:
      master   -> /master/ only                      (reads data, manages users & settings)
      admin    -> the admin app (inventory, products, suppliers, import ...) but NOT /pos/, /master/
                  or the scan / return pages
      operator -> only the scan / PDF / return pages (OPERATOR_URL_NAMES)
      cashier  -> /pos/ only
    Login, logout, change-password and static/media files are open to everybody who is signed in.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _common(self):
        return [reverse('login'), reverse('logout'), reverse('password_change'),
                getattr(settings, 'STATIC_URL', '/static/') or '/static/',
                getattr(settings, 'MEDIA_URL', '/media/') or '/media/']

    @staticmethod
    def _allowed(role, match):
        app = match.func.__module__.split('.')[0]
        if app == 'accounts':                 # role_home / operator_home guard themselves
            return True
        if role == MASTER:
            return app in ('masteradmin', 'django')
        if role == CASHIER:
            return app == 'pos'
        if role == OPERATOR:
            return match.url_name in OPERATOR_URL_NAMES
        if role == ADMIN:
            return app not in ('masteradmin', 'pos', 'django') and match.url_name not in OPERATOR_URL_NAMES
        return False

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            path = request.path
            if not any(path.startswith(p) for p in self._common()):
                try:
                    match = resolve(request.path_info)
                except Resolver404:
                    match = None
                if match is not None:
                    role = get_role(user)
                    if not self._allowed(role, match):
                        target = reverse(home_name(user))
                        if path != target:
                            messages.error(request, 'That page belongs to a different account type.', fail_silently=True)
                            return redirect(target)
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

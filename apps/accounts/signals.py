from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .audit import log


@receiver(user_logged_in)
def _in(sender, request, user, **kw):
    if request is not None:
        log(request, 'login', 'Signed in', user=user)


@receiver(user_logged_out)
def _out(sender, request, user, **kw):
    if request is not None and user is not None:
        log(request, 'logout', 'Signed out', user=user)


@receiver(user_login_failed)
def _bad(sender, credentials, request=None, **kw):
    if request is not None:
        log(request, 'login_failed', 'Wrong username or password', username=str(credentials.get('username', ''))[:150])

from .models import AuditLog
from .roles import get_role


def client_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    return (fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR')) or None


def log(request, action, detail='', user=None, username='', status=None):
    """Write one audit row. Marks the request so the generic middleware does not log it twice."""
    user = user if user is not None else getattr(request, 'user', None)
    if user is not None and not getattr(user, 'is_authenticated', False):
        user = None
    try:
        AuditLog.objects.create(
            user=user, username=(user.get_username() if user else username)[:150],
            role=(get_role(user) or '') if user else '', action=action[:30], detail=str(detail)[:500],
            path=request.path[:300], method=request.method[:8], status_code=status, ip=client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:255])
        request._audited = True
    except Exception:       # logging must never break a sale or a login
        pass

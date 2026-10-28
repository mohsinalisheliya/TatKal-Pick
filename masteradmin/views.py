import csv
import secrets
import string
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts import audit
from accounts.decorators import role_required
from accounts.models import AuditLog, UserProfile
from accounts.roles import ROLE_LABELS, TIERS, TIER_KEYS, get_role, parse_tiers
from pos.models import PosSale

User = get_user_model()
master_only = role_required('master')


def _apply_role(user, role):
    """Keep Django's own flags in step with the role (admin site, createsuperuser, etc.)."""
    user.is_staff = role in ('master', 'admin')
    user.is_superuser = role == 'master'


def _temp_password():
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))


@master_only
def home(request):
    now = timezone.now()
    today = timezone.localdate()
    roles = {'master': 0, 'admin': 0, 'cashier': 0}
    users = list(User.objects.select_related('profile'))
    for u in users:
        roles[get_role(u)] = roles.get(get_role(u), 0) + 1
    paid = PosSale.objects.filter(created_at__date=today, status='paid')
    agg = paid.aggregate(t=Sum('total'), n=Count('pk'))
    since = now - timedelta(minutes=30)
    active_now = AuditLog.objects.filter(created_at__gte=since).exclude(username='').values('username').distinct().count()
    by_cashier = list(paid.values('cashier_name').annotate(t=Sum('total'), n=Count('pk')).order_by('-t')[:6])
    return render(request, 'masteradmin/home.html', {
        'tab': 'home', 'roles': roles, 'users_total': len(users), 'inactive': sum(1 for u in users if not u.is_active),
        'sales_today': agg['t'] or 0, 'bills_today': agg['n'] or 0, 'active_now': active_now,
        'failed_24h': AuditLog.objects.filter(action='login_failed', created_at__gte=now - timedelta(hours=24)).count(),
        'logins_today': AuditLog.objects.filter(action='login', created_at__date=today).count(),
        'by_cashier': by_cashier, 'recent': AuditLog.objects.all()[:12]})


@master_only
def users(request):
    rows = []
    for u in User.objects.select_related('profile').order_by('-is_active', 'username'):
        last = AuditLog.objects.filter(user=u, action='login').first()
        role = get_role(u)
        prof = getattr(u, 'profile', None)
        rows.append({'u': u, 'role': role, 'role_label': ROLE_LABELS[role], 'last_ip': last.ip if last else '',
                     'last_login': u.last_login, 'tiers': parse_tiers(prof.price_tiers) if (prof and role == 'cashier') else None})
    return render(request, 'masteradmin/users.html', {'tab': 'users', 'rows': rows})


def _form_data(post, user=None):
    d = {
        'username': post.get('username', '').strip(), 'first_name': post.get('first_name', '').strip(),
        'email': post.get('email', '').strip(), 'role': post.get('role', 'cashier'),
        'tiers': [t for t in post.getlist('tiers') if t in TIER_KEYS], 'default_tier': post.get('default_tier', 'retail'),
        'max_discount': post.get('max_discount', '').strip(), 'note': post.get('note', '').strip()[:200],
        'is_active': post.get('is_active') == 'on', 'password': post.get('password', ''),
    }
    return d


@master_only
def user_form(request, pk=None):
    target = get_object_or_404(User.objects.select_related('profile'), pk=pk) if pk else None
    prof = getattr(target, 'profile', None) if target else None
    is_self = bool(target and target.pk == request.user.pk)
    if request.method == 'POST':
        d = _form_data(request.POST)
        errors = []
        if d['role'] not in ROLE_LABELS:
            errors.append('Choose a role.')
        if not target:
            if not d['username']:
                errors.append('Username is required.')
            elif User.objects.filter(username__iexact=d['username']).exists():
                errors.append('That username is already taken.')
            if len(d['password']) < 6:
                errors.append('Password must be at least 6 characters.')
        if d['role'] == 'cashier' and not d['tiers']:
            errors.append('Pick at least one price list for this cashier.')
        md = None
        if d['max_discount'] != '':
            if d['max_discount'].isdigit() and 0 <= int(d['max_discount']) <= 100:
                md = int(d['max_discount'])
            else:
                errors.append('Max discount must be a whole number from 0 to 100.')
        if target:
            if is_self and (d['role'] != 'master' or not d['is_active']):
                errors.append('You cannot demote or disable your own account.')
            elif get_role(target) == 'master' and d['role'] != 'master' and \
                    User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk).count() == 0:
                errors.append('There must always be at least one active Master Admin.')
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            created = target is None
            old_role = get_role(target) if target else None
            user = target or User(username=d['username'])
            user.first_name, user.email = d['first_name'], d['email']
            user.is_active = True if created else d['is_active']
            _apply_role(user, d['role'])
            if created:
                user.set_password(d['password'])
            user.save()
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'created_by': request.user})
            profile.role, profile.note, profile.max_discount = d['role'], d['note'], md
            tiers = d['tiers'] if d['role'] == 'cashier' else list(TIER_KEYS)
            profile.price_tiers = ','.join(tiers)
            profile.default_tier = d['default_tier'] if d['default_tier'] in tiers else tiers[0]
            profile.save()
            if created:
                audit.log(request, 'user_create', f'Created {d["role"]} "{user.username}"'
                          + (f' (price lists: {profile.price_tiers})' if d['role'] == 'cashier' else ''))
                messages.success(request, f'User "{user.username}" created.')
            else:
                bits = []
                if old_role != d['role']:
                    bits.append(f'role {old_role} -> {d["role"]}')
                bits.append(f'price lists: {profile.price_tiers}' if d['role'] == 'cashier' else '')
                audit.log(request, 'user_update', f'Updated "{user.username}" ' + '; '.join(b for b in bits if b))
                messages.success(request, f'"{user.username}" saved.')
            return redirect('master_users')
    cur = {'role': get_role(target) if target else 'cashier',
           'tiers': parse_tiers(prof.price_tiers) if prof else ['retail'],
           'default_tier': prof.default_tier if prof else 'retail',
           'max_discount': prof.max_discount if (prof and prof.max_discount is not None) else '',
           'note': prof.note if prof else '', 'is_active': target.is_active if target else True}
    if request.method == 'POST':
        cur.update(role=d['role'], tiers=d['tiers'], default_tier=d['default_tier'], max_discount=d['max_discount'],
                   note=d['note'], is_active=d['is_active'])
    return render(request, 'masteradmin/user_form.html', {
        'tab': 'users', 'target': target, 'cur': cur, 'is_self': is_self, 'tiers': TIERS,
        'roles': list(ROLE_LABELS.items()), 'post': request.POST if request.method == 'POST' else None})


@master_only
@require_POST
def user_reset_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    pw = _temp_password()
    user.set_password(pw)
    user.save(update_fields=['password'])
    audit.log(request, 'password_reset', f'Reset password for "{user.username}"')
    messages.success(request, f'New password for "{user.username}": {pw}  - copy it now, it is shown only once.')
    return redirect('master_users')


@master_only
@require_POST
def user_toggle(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, 'You cannot disable your own account.')
    elif user.is_active and user.is_superuser and not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=user.pk).exists():
        messages.error(request, 'There must always be at least one active Master Admin.')
    else:
        user.is_active = not user.is_active
        user.save(update_fields=['is_active'])
        audit.log(request, 'user_enable' if user.is_active else 'user_disable',
                  f'{"Enabled" if user.is_active else "Disabled"} "{user.username}"')
        messages.success(request, f'"{user.username}" is now {"active" if user.is_active else "disabled"}.')
    return redirect('master_users')


def _filtered_logs(request):
    qs = AuditLog.objects.all()
    g = request.GET
    if g.get('user'):
        qs = qs.filter(username=g['user'])
    if g.get('action'):
        qs = qs.filter(action=g['action'])
    if g.get('role'):
        qs = qs.filter(role=g['role'])
    if g.get('from'):
        qs = qs.filter(created_at__date__gte=g['from'])
    if g.get('to'):
        qs = qs.filter(created_at__date__lte=g['to'])
    if g.get('q'):
        qs = qs.filter(Q(detail__icontains=g['q']) | Q(path__icontains=g['q']) | Q(ip__icontains=g['q']))
    return qs


@master_only
def logs(request):
    try:
        qs = _filtered_logs(request)
        qs.exists()
    except Exception:       # a malformed date in the URL
        qs = AuditLog.objects.none()
        messages.error(request, 'Those filters are not valid.')
    if request.GET.get('export') == '1':
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = 'attachment; filename="activity-log.csv"'
        w = csv.writer(resp)
        w.writerow(['Time', 'User', 'Role', 'Action', 'Detail', 'Method', 'Path', 'Status', 'IP', 'Browser'])
        for r in qs[:5000]:
            w.writerow([timezone.localtime(r.created_at).strftime('%Y-%m-%d %H:%M:%S'), r.username, r.role, r.action,
                        r.detail, r.method, r.path, r.status_code or '', r.ip or '', r.user_agent])
        return resp
    page = Paginator(qs, 50).get_page(request.GET.get('page'))
    keep = request.GET.copy()
    keep.pop('page', None)
    return render(request, 'masteradmin/logs.html', {
        'tab': 'logs', 'page': page, 'total': page.paginator.count, 'qs': keep.urlencode(), 'g': request.GET,
        'names': list(User.objects.order_by('username').values_list('username', flat=True)),
        'actions': list(AuditLog.objects.order_by('action').values_list('action', flat=True).distinct())})

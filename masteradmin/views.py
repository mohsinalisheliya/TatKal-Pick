import csv
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.db.models import Count, F, Q, Sum, DecimalField, ExpressionWrapper
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts import audit
from accounts.decorators import role_required
from accounts.models import AuditLog, UserProfile
from accounts.roles import ROLE_LABELS, TIERS, TIER_KEYS, get_role, normalize_tier, parse_tiers
from pos.models import PosSale
from sku_manager.models import AppSettings, DispatchLog, JewelrySKU, StockBatch

User = get_user_model()
master_only = role_required('master')


def _apply_role(user, role):
    """Keep Django's own flags in step with the role (admin site, createsuperuser, etc.)."""
    user.is_staff = role in ('master', 'admin')
    user.is_superuser = role == 'master'


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
    stock_agg = StockBatch.objects.filter(status='active', quantity__gt=0).aggregate(
        u=Sum('quantity'), c=Sum(ExpressionWrapper(F('quantity') * F('purchase_price'),
                                                   output_field=DecimalField(max_digits=14, decimal_places=2))))
    dispatched = DispatchLog.objects.filter(dispatched_at__date=today).exclude(platform_name='Counter (POS)').aggregate(
        u=Sum('quantity'))['u'] or 0
    by_cashier = list(paid.values('cashier_name').annotate(t=Sum('total'), n=Count('pk')).order_by('-t')[:6])
    return render(request, 'masteradmin/home.html', {
        'tab': 'home', 'roles': roles, 'users_total': len(users), 'inactive': sum(1 for u in users if not u.is_active),
        'sales_today': agg['t'] or 0, 'bills_today': agg['n'] or 0, 'active_now': active_now,
        'failed_24h': AuditLog.objects.filter(action='login_failed', created_at__gte=now - timedelta(hours=24)).count(),
        'logins_today': AuditLog.objects.filter(action='login', created_at__date=today).count(),
        'stock_units': stock_agg['u'] or 0, 'stock_cost': stock_agg['c'] or 0, 'dispatched_today': dispatched,
        'by_cashier': by_cashier, 'recent': AuditLog.objects.all()[:12]})


@master_only
def users(request):
    rows = []
    for u in User.objects.select_related('profile').order_by('-is_active', 'username'):
        last = AuditLog.objects.filter(user=u, action='login').first()
        role = get_role(u)
        prof = getattr(u, 'profile', None)
        rows.append({'u': u, 'role': role, 'role_label': ROLE_LABELS[role], 'last_ip': last.ip if last else '',
                     'last_login': u.last_login, 'tiers': parse_tiers(prof.price_tiers) if (prof and role == 'cashier') else None,
                     'can_void': bool(prof and role == 'cashier' and prof.can_void)})
    return render(request, 'masteradmin/users.html', {'tab': 'users', 'rows': rows})


def _form_data(post):
    return {
        'username': post.get('username', '').strip(), 'first_name': post.get('first_name', '').strip(),
        'email': post.get('email', '').strip(), 'phone': post.get('phone', '').strip()[:20],
        'role': post.get('role', 'cashier'),
        'tiers': [t for t in post.getlist('tiers') if normalize_tier(t) in TIER_KEYS],
        'default_tier': post.get('default_tier', 'retail'),
        'max_discount': post.get('max_discount', '').strip(), 'note': post.get('note', '').strip()[:200],
        'is_active': post.get('is_active') == 'on', 'can_void': post.get('can_void') == 'on',
        'password': post.get('password', ''), 'password2': post.get('password2', ''),
    }


def _password_errors(pw, pw2):
    if len(pw) < 6:
        return ['Password must be at least 6 characters.']
    if pw != pw2:
        return ['The two passwords do not match.']
    return []


@master_only
def user_form(request, pk=None):
    """Create a user, or edit everything about one: username, name, email, phone, role, price lists, password."""
    target = get_object_or_404(User.objects.select_related('profile'), pk=pk) if pk else None
    prof = getattr(target, 'profile', None) if target else None
    is_self = bool(target and target.pk == request.user.pk)
    if request.method == 'POST':
        d = _form_data(request.POST)
        if is_self:
            d['role'], d['is_active'] = 'master', True
        errors = []
        if d['role'] not in ROLE_LABELS:
            errors.append('Choose a role.')
        if not d['username'] or len(d['username']) > 150 or ' ' in d['username']:
            errors.append('Username is required (no spaces, max 150 characters).')
        elif User.objects.filter(username__iexact=d['username']).exclude(pk=target.pk if target else None).exists():
            errors.append('That username is already taken.')
        if d['email']:
            try:
                validate_email(d['email'])
            except ValidationError:
                errors.append('Enter a valid email address.')
        if not target or d['password'] or d['password2']:
            errors += _password_errors(d['password'], d['password2'])
        if d['role'] == 'cashier' and not d['tiers']:
            errors.append('Pick at least one price list for this cashier.')
        md = None
        if d['max_discount'] != '':
            if d['max_discount'].isdigit() and 0 <= int(d['max_discount']) <= 100:
                md = int(d['max_discount'])
            else:
                errors.append('Max discount must be a whole number from 0 to 100.')
        if target and not is_self and get_role(target) == 'master' and d['role'] != 'master' and \
                User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk).count() == 0:
            errors.append('There must always be at least one active Master Admin.')
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            created = target is None
            old = {'role': get_role(target), 'username': target.username} if target else {}
            user = target or User()
            user.username, user.first_name, user.email = d['username'], d['first_name'], d['email']
            user.is_active = True if created else d['is_active']
            _apply_role(user, d['role'])
            pw_changed = bool(d['password'])
            if pw_changed:
                user.set_password(d['password'])
            user.save()
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'created_by': request.user})
            profile.role, profile.note, profile.max_discount, profile.phone = d['role'], d['note'], md, d['phone']
            tiers = [normalize_tier(t) for t in d['tiers']] if d['role'] == 'cashier' else list(TIER_KEYS)
            profile.price_tiers = ','.join(dict.fromkeys(tiers))
            dt = normalize_tier(d['default_tier'])
            profile.default_tier = dt if dt in tiers else tiers[0]
            profile.can_void = d['can_void'] if d['role'] == 'cashier' else False
            profile.save()
            if created:
                audit.log(request, 'user_create', f'Created {d["role"]} "{user.username}"'
                          + (f' (price lists: {profile.price_tiers})' if d['role'] == 'cashier' else ''))
                messages.success(request, f'User "{user.username}" created.')
            else:
                bits = []
                if old['role'] != d['role']:
                    bits.append(f'role {old["role"]} -> {d["role"]}')
                if old['username'] != user.username:
                    bits.append(f'username {old["username"]} -> {user.username}')
                if pw_changed:
                    bits.append('password changed')
                if d['role'] == 'cashier':
                    bits.append(f'price lists: {profile.price_tiers}')
                audit.log(request, 'user_update', f'Updated "{user.username}"' + (': ' + '; '.join(bits) if bits else ''))
                messages.success(request, f'"{user.username}" saved.')
                if is_self and pw_changed:
                    from django.contrib.auth import update_session_auth_hash
                    update_session_auth_hash(request, user)
            return redirect('master_users')
    cur = {'role': get_role(target) if target else 'cashier',
           'tiers': parse_tiers(prof.price_tiers) if prof else ['retail'],
           'default_tier': normalize_tier(prof.default_tier) if prof else 'retail',
           'max_discount': prof.max_discount if (prof and prof.max_discount is not None) else '',
           'note': prof.note if prof else '', 'phone': prof.phone if prof else '',
           'can_void': prof.can_void if prof else False,
           'is_active': target.is_active if target else True}
    post = request.POST if request.method == 'POST' else None
    if post:
        cur.update(role=d['role'], tiers=d['tiers'], default_tier=d['default_tier'], max_discount=d['max_discount'],
                   note=d['note'], phone=d['phone'], is_active=d['is_active'], can_void=d['can_void'])
    return render(request, 'masteradmin/user_form.html', {
        'tab': 'users', 'target': target, 'cur': cur, 'is_self': is_self, 'tiers': TIERS,
        'roles': list(ROLE_LABELS.items()), 'post': post})


@master_only
def user_set_password(request, pk):
    """The Master Admin types the new password themselves (nothing is generated)."""
    user = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        pw, pw2 = request.POST.get('password', ''), request.POST.get('password2', '')
        errors = _password_errors(pw, pw2)
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            user.set_password(pw)
            user.save(update_fields=['password'])
            audit.log(request, 'password_reset', f'Set a new password for "{user.username}"')
            messages.success(request, f'Password for "{user.username}" updated.')
            if user.pk == request.user.pk:
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, user)
            return redirect('master_users')
    return render(request, 'masteradmin/set_password.html', {'tab': 'users', 'target': user})


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


# ---------------------------------------------------------------- System settings (Master only)
@master_only
def app_settings(request):
    obj = AppSettings.get_settings()
    if request.method == 'POST':
        p = request.POST
        obj.brand_name = p.get('brand_name', '').strip() or 'TATKAL PICK'
        obj.tagline = p.get('tagline', '').strip()
        obj.currency_symbol = p.get('currency_symbol', '₹').strip() or '₹'
        th = p.get('low_stock_threshold', '5').strip()
        obj.low_stock_threshold = int(th) if th.isdigit() else 5
        obj.support_contact = p.get('support_contact', '').strip()
        check, bad = URLValidator(schemes=['http', 'https']), []
        for field in ('meesho_store_url', 'amazon_store_url', 'flipkart_store_url'):
            value = p.get(field, '').strip()
            if value:
                try:
                    check(value)
                except ValidationError:
                    bad.append(field.split('_')[0].title())
            setattr(obj, field, value)
        if bad:
            messages.error(request, 'Enter a valid http:// or https:// URL for: ' + ', '.join(bad) + '.')
            return render(request, 'masteradmin/settings.html', {'tab': 'settings', 'settings': obj})
        logo = request.FILES.get('label_logo')
        if logo:
            obj.label_logo = logo
        elif p.get('remove_label_logo') and obj.label_logo:
            obj.label_logo.delete(save=False)
            obj.label_logo = None
        obj.save()
        audit.log(request, 'settings_update', 'Changed system settings')
        messages.success(request, 'Settings saved.')
        return redirect('app_settings')
    return render(request, 'masteradmin/settings.html', {'tab': 'settings', 'settings': obj})


# ---------------------------------------------------------------- Read-only data
def _day(request):
    raw = request.GET.get('date', '')
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date() if raw else timezone.localdate()
    except ValueError:
        return timezone.localdate()


@master_only
def sales(request):
    """Every counter bill, read-only."""
    day = _day(request)
    qs = PosSale.objects.filter(created_at__date=day).prefetch_related('items')
    who = request.GET.get('cashier', '')
    if who:
        qs = qs.filter(cashier_name=who)
    paid = qs.filter(status='paid')
    agg = paid.aggregate(t=Sum('total'), n=Count('pk'), d=Sum('discount_amount'))
    pay = {r['payment_method']: r['t'] for r in paid.values('payment_method').annotate(t=Sum('total'))}
    cashiers = list(PosSale.objects.exclude(cashier_name='').order_by('cashier_name').values_list('cashier_name', flat=True).distinct())
    # marketplace dispatches of the same day, for context
    market = list(DispatchLog.objects.filter(dispatched_at__date=day).exclude(platform_name='Counter (POS)')
                  .values('platform_name').annotate(units=Sum('quantity'), n=Count('pk')).order_by('-units'))
    return render(request, 'masteradmin/sales.html', {
        'tab': 'sales', 'day': day, 'sales': qs[:400], 'revenue': agg['t'] or 0, 'bills': agg['n'] or 0,
        'discount': agg['d'] or 0, 'cash': pay.get('cash', 0), 'upi': pay.get('upi', 0), 'card': pay.get('card', 0),
        'cashiers': cashiers, 'who': who, 'market': market})


@master_only
def sale_detail(request, pk):
    sale = get_object_or_404(PosSale.objects.prefetch_related('items'), pk=pk)
    return render(request, 'masteradmin/sale_detail.html', {'tab': 'sales', 'sale': sale})


@master_only
def stock(request):
    """Stock overview, read-only: units and value per product."""
    value = ExpressionWrapper(F('quantity') * F('purchase_price'), output_field=DecimalField(max_digits=14, decimal_places=2))
    batches = (StockBatch.objects.filter(status='active', quantity__gt=0)
               .values('sku_id').annotate(units=Sum('quantity'), cost=Sum(value), n=Count('pk')))
    by_sku = {b['sku_id']: b for b in batches}
    threshold = AppSettings.get_settings().low_stock_threshold
    q = request.GET.get('q', '').strip()
    prods = JewelrySKU.objects.all().order_by('name')
    if q:
        prods = prods.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(category__icontains=q))
    rows, total_units, total_cost, low, out = [], 0, Decimal('0'), 0, 0
    for p in prods:
        b = by_sku.get(p.pk, {})
        units, cost = b.get('units', 0) or 0, b.get('cost') or Decimal('0')
        total_units += units
        total_cost += cost
        status = 'out' if p.stock <= 0 else 'low' if p.stock <= threshold else 'ok'
        low += status == 'low'
        out += status == 'out'
        rows.append({'p': p, 'batches': b.get('n', 0), 'units': units, 'cost': cost, 'status': status,
                     'resell': (p.selling_price or 0) * p.stock})
    return render(request, 'masteradmin/stock.html', {
        'tab': 'stock', 'rows': rows, 'q': q, 'total_units': total_units, 'total_cost': total_cost,
        'low': low, 'out': out, 'products': len(rows),
        'resell_value': sum((r['resell'] for r in rows), Decimal('0'))})

import json
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog, UserProfile
from accounts.roles import get_role
from pos.models import PosSale, ResellerPrice
from sku_manager.models import JewelrySKU, StockBatch

U = get_user_model()


class Base(TestCase):
    def setUp(self):
        call_command('create_user', 'boss', 'bosspass', '--role', 'master', '--name', 'Owner')
        call_command('create_user', 'adam', 'adampass', '--role', 'admin')
        call_command('create_user', 'ravi', 'ravipass', '--role', 'cashier', '--tiers', 'retail,reseller')
        call_command('create_user', 'sam', 'sampass', '--role', 'cashier', '--tiers', 'wholesale')
        call_command('create_user', 'cost', 'costpass', '--role', 'cashier', '--tiers', 'buying')
        self.boss, self.adam, self.ravi, self.sam, self.cost = (U.objects.get(username=n) for n in ('boss', 'adam', 'ravi', 'sam', 'cost'))
        self.ring = JewelrySKU.objects.create(name='Ring', purchase_price=10, wholesale_price=30, selling_price=50)
        StockBatch.objects.create(sku=self.ring, batch_no='A', quantity=20, purchase_price=10, wholesale_price=30, selling_price=50)
        self.ring.sync_stock_from_batches()

    def sell(self, tier, qty=1, **kw):
        body = dict(items=[{'id': self.ring.pk, 'qty': qty}], price_list=tier, **kw)
        return self.client.post(reverse('pos_checkout'), json.dumps(body), content_type='application/json')


class RoleRoutingTests(Base):
    def login(self, name, pw):
        return self.client.post(reverse('login'), {'username': name, 'password': pw})

    def test_each_role_lands_on_its_own_app(self):
        self.assertRedirects(self.login('boss', 'bosspass'), '/master/', fetch_redirect_response=False)
        self.client.logout()
        self.assertRedirects(self.login('adam', 'adampass'), '/', fetch_redirect_response=False)
        self.client.logout()
        self.assertRedirects(self.login('ravi', 'ravipass'), '/pos/', fetch_redirect_response=False)

    def test_cashier_is_locked_to_pos(self):
        self.client.force_login(self.ravi)
        for url in ('/', '/products/', '/master/', '/master/users/', '/analytics/products/'):
            self.assertRedirects(self.client.get(url), '/pos/', fetch_redirect_response=False, msg_prefix=url)
        self.assertEqual(self.client.get('/pos/').status_code, 200)
        self.assertEqual(self.client.get('/pos/sales/').status_code, 200)
        self.assertEqual(self.client.get('/pos/prices/').status_code, 302)       # managers only

    def test_admin_cannot_open_master(self):
        self.client.force_login(self.adam)
        for url in ('/master/', '/master/users/', '/master/logs/'):
            self.assertRedirects(self.client.get(url), '/', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/pos/').status_code, 200)
        self.assertEqual(self.client.get('/pos/prices/').status_code, 200)

    def test_master_opens_everything(self):
        self.client.force_login(self.boss)
        for url in ('/master/', '/master/users/', '/master/logs/', '/master/users/new/', f'/master/users/{self.ravi.pk}/', '/pos/', '/pos/sales/', '/pos/prices/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_anonymous_goes_to_login(self):
        for url in ('/pos/', '/master/', '/pos/api/products/'):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302); self.assertIn('/login/', r['Location'])

    def test_user_without_profile_is_admin_and_superuser_is_master(self):
        legacy = U.objects.create_user('old', password='x', is_staff=True)
        self.assertEqual(get_role(legacy), 'admin')
        sup = U.objects.create_superuser('sup', password='x')
        self.assertEqual(get_role(sup), 'master')

    def test_disabled_account_cannot_sign_in(self):
        self.ravi.is_active = False; self.ravi.save()
        r = self.login('ravi', 'ravipass')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_next_is_honoured_only_when_safe(self):
        r = self.client.post(reverse('login') + '?next=https://evil.example/', {'username': 'ravi', 'password': 'ravipass'})
        self.assertEqual(r['Location'], '/pos/')

    def test_logout_works_with_get(self):
        self.client.force_login(self.ravi)
        self.assertRedirects(self.client.get('/logout/'), '/login/', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/pos/').status_code, 302)


class PriceTierTests(Base):
    def test_api_only_contains_allowed_prices_and_never_leaks_cost(self):
        self.client.force_login(self.ravi)
        body = self.client.get('/pos/api/products/').content.decode()
        p = json.loads(body)['products'][0]
        self.assertEqual(set(p['prices']), {'retail', 'reseller'})
        self.assertNotIn('purchase', body); self.assertNotIn('"wholesale"', body)
        self.client.force_login(self.adam)
        self.assertEqual(set(self.client.get('/pos/api/products/').json()['products'][0]['prices']),
                         {'retail', 'reseller', 'wholesale', 'buying'})

    def test_cashier_cannot_sell_on_a_tier_they_do_not_have(self):
        self.client.force_login(self.ravi)
        r = self.sell('wholesale')
        self.assertEqual(r.status_code, 400); self.assertIn('not allowed', r.json()['message'])
        self.assertEqual(self.sell('buying').status_code, 400)
        self.assertEqual(PosSale.objects.count(), 0)
        self.ring.refresh_from_db(); self.assertEqual(self.ring.stock, 20)

    def test_each_tier_price(self):
        self.client.force_login(self.ravi)
        j = self.sell('retail').json(); self.assertEqual(j['total'], '50.00')
        j = self.sell('reseller').json(); self.assertEqual(j['total'], '33.00')        # wholesale 30 + 10% markup
        ResellerPrice.objects.create(sku=self.ring, price=D('38.50'))
        j = self.sell('reseller').json(); self.assertEqual(j['total'], '38.50')
        self.client.force_login(self.sam)
        j = self.sell('wholesale', qty=2).json(); self.assertEqual(j['total'], '60.00')
        self.client.force_login(self.cost)
        j = self.sell('buying', qty=2).json(); self.assertEqual(j['total'], '20.00')
        self.assertEqual(self.sell('buying', discount_percent=5).status_code, 400)

    def test_discount_limits(self):
        self.client.force_login(self.ravi)
        self.assertEqual(self.sell('retail', discount_percent=11).status_code, 400)       # default cap 10
        self.assertTrue(self.sell('retail', discount_percent=10).json()['ok'])
        p = self.ravi.profile; p.max_discount = 3; p.save()
        self.assertEqual(self.sell('retail', discount_percent=5).status_code, 400)
        self.client.force_login(self.adam)
        self.assertTrue(self.sell('retail', discount_percent=40).json()['ok'])

    def test_void_is_manager_only_and_restores_stock(self):
        self.client.force_login(self.ravi)
        self.sell('retail', qty=3)
        sale = PosSale.objects.get()
        self.client.post(reverse('pos_void', args=[sale.pk]))
        sale.refresh_from_db(); self.assertEqual(sale.status, 'paid')
        self.client.force_login(self.adam)
        self.client.post(reverse('pos_void', args=[sale.pk]))
        sale.refresh_from_db(); self.ring.refresh_from_db()
        self.assertEqual((sale.status, self.ring.stock), ('void', 20))

    def test_cashier_sees_only_own_sales(self):
        self.client.force_login(self.ravi); self.sell('retail')
        mine = PosSale.objects.get()
        self.client.force_login(self.sam); self.sell('wholesale')
        self.assertEqual(self.client.get(reverse('pos_receipt', args=[mine.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('pos_receipt', args=[PosSale.objects.exclude(pk=mine.pk).get().pk])).status_code, 200)

    def test_prices_page_saves_reseller_price(self):
        self.client.force_login(self.adam)
        self.client.post('/pos/prices/', {f'reseller_{self.ring.pk}': '41'})
        self.assertEqual(ResellerPrice.objects.get().price, D('41.00'))
        self.client.post('/pos/prices/', {f'reseller_{self.ring.pk}': ''})
        self.assertEqual(ResellerPrice.objects.count(), 0)


class MasterConsoleTests(Base):
    def setUp(self):
        super().setUp(); self.client.force_login(self.boss)

    def test_create_cashier_with_tiers(self):
        r = self.client.post('/master/users/new/', {'username': 'meena', 'first_name': 'Meena', 'password': 'secret1', 'role': 'cashier',
                                                     'tiers': ['wholesale', 'reseller'], 'default_tier': 'wholesale', 'max_discount': '5', 'is_active': 'on'})
        self.assertEqual(r.status_code, 302)
        u = U.objects.get(username='meena')
        self.assertTrue(u.check_password('secret1')); self.assertNotEqual(u.password, 'secret1')   # hashed
        self.assertEqual((u.profile.role, u.profile.price_tiers, u.profile.max_discount, u.is_staff), ('cashier', 'wholesale,reseller', 5, False))
        self.client.logout()
        self.client.post('/login/', {'username': 'meena', 'password': 'secret1'})
        self.assertEqual(set(self.client.get('/pos/api/products/').json()['products'][0]['prices']), {'wholesale', 'reseller'})

    def test_validation(self):
        base = {'username': 'x1', 'password': 'secret1', 'role': 'cashier', 'tiers': ['retail']}
        self.assertEqual(self.client.post('/master/users/new/', dict(base, username='ravi')).status_code, 200)    # duplicate
        self.assertEqual(self.client.post('/master/users/new/', dict(base, password='abc')).status_code, 200)     # short
        self.assertEqual(self.client.post('/master/users/new/', dict(base, tiers=[])).status_code, 200)           # no tiers
        self.assertEqual(self.client.post('/master/users/new/', dict(base, max_discount='500')).status_code, 200)
        self.assertFalse(U.objects.filter(username='x1').exists())

    def test_edit_role_and_tiers(self):
        self.client.post(f'/master/users/{self.ravi.pk}/', {'role': 'cashier', 'tiers': ['buying', 'retail'], 'default_tier': 'buying', 'is_active': 'on'})
        self.ravi.profile.refresh_from_db(); self.assertEqual(self.ravi.profile.price_tiers, 'buying,retail')
        self.client.post(f'/master/users/{self.ravi.pk}/', {'role': 'admin', 'is_active': 'on'})
        self.ravi.refresh_from_db(); self.assertEqual((get_role(self.ravi), self.ravi.is_staff, self.ravi.is_superuser), ('admin', True, False))

    def test_reset_password(self):
        r = self.client.post(f'/master/users/{self.ravi.pk}/reset-password/', follow=True)
        msg = [str(m) for m in r.context['messages']][0]
        new = msg.split(': ')[1].split()[0]
        self.ravi.refresh_from_db()
        self.assertTrue(self.ravi.check_password(new)); self.assertFalse(self.ravi.check_password('ravipass'))

    def test_disable_enable_and_self_protection(self):
        self.client.post(f'/master/users/{self.ravi.pk}/toggle/')
        self.ravi.refresh_from_db(); self.assertFalse(self.ravi.is_active)
        self.client.post(f'/master/users/{self.boss.pk}/toggle/')
        self.boss.refresh_from_db(); self.assertTrue(self.boss.is_active)
        self.client.post(f'/master/users/{self.boss.pk}/', {'role': 'admin', 'is_active': 'on'})
        self.assertEqual(get_role(U.objects.get(pk=self.boss.pk)), 'master')

    def test_last_master_cannot_be_demoted_by_another_master(self):
        m2 = U.objects.create_superuser('m2', password='x')
        self.client.force_login(m2)
        self.client.post(f'/master/users/{self.boss.pk}/', {'role': 'admin', 'is_active': 'on'})
        self.assertEqual(get_role(U.objects.get(pk=self.boss.pk)), 'admin')       # ok, m2 still master
        self.client.post(f'/master/users/{m2.pk}/', {'role': 'admin', 'is_active': 'on'})
        self.assertEqual(get_role(U.objects.get(pk=m2.pk)), 'master')             # self-demotion refused


class AuditTests(Base):
    def test_events_are_recorded(self):
        self.client.post('/login/', {'username': 'ravi', 'password': 'WRONG'})
        self.client.post('/login/', {'username': 'ravi', 'password': 'ravipass'})
        self.sell('retail', qty=2)
        self.client.get('/logout/')
        acts = list(AuditLog.objects.order_by('pk').values_list('action', 'username'))
        self.assertEqual(acts, [('login_failed', 'ravi'), ('login', 'ravi'), ('sale', 'ravi'), ('logout', 'ravi')])
        sale = AuditLog.objects.get(action='sale'); self.assertIn('TP', sale.detail); self.assertEqual(sale.role, 'cashier')

    def test_master_actions_logged_and_no_password_leaks(self):
        self.client.force_login(self.boss)
        self.client.post('/master/users/new/', {'username': 'n1', 'password': 'topsecret', 'role': 'cashier', 'tiers': ['retail']})
        self.client.post(f'/master/users/{self.ravi.pk}/reset-password/')
        text = ' '.join(f'{a.detail} {a.path}' for a in AuditLog.objects.all())
        self.assertNotIn('topsecret', text)
        self.assertEqual(AuditLog.objects.filter(action__in=['user_create', 'password_reset']).count(), 2)

    def test_admin_changes_are_logged_generically(self):
        self.client.force_login(self.adam)
        self.client.post('/inventory/threshold/', {'threshold': '5'})
        self.assertTrue(AuditLog.objects.filter(username='adam', action='change').exists())

    def test_log_page_filters_export_and_access(self):
        self.client.force_login(self.ravi); self.sell('retail')
        self.client.force_login(self.sam); self.sell('wholesale')
        self.client.force_login(self.boss)
        r = self.client.get('/master/logs/?user=ravi&action=sale')
        self.assertEqual(r.context['total'], 1)
        self.assertEqual(self.client.get('/master/logs/?from=garbage').status_code, 200)
        csv = self.client.get('/master/logs/?export=1')
        self.assertIn('attachment', csv['Content-Disposition']); self.assertIn('ravi', csv.content.decode())
        for u in (self.adam, self.ravi):
            self.client.force_login(u)
            self.assertEqual(self.client.get('/master/logs/').status_code, 302)

    def test_overview_page(self):
        self.client.force_login(self.ravi); self.sell('retail')
        self.client.force_login(self.boss)
        r = self.client.get('/master/')
        self.assertEqual((r.context['bills_today'], r.context['users_total']), (1, 5))

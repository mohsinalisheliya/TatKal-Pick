"""Tests for roles, counter, master console, suppliers, stock form and import.
Run:  python manage.py test accounts"""
import io
import json
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import resolve, reverse

from accounts.middleware import RoleAccessMiddleware
from accounts.models import AuditLog
from accounts.roles import allowed_tiers, get_role, parse_tiers
from importer.models import ImportJob
from pos.models import PosSale
from sku_manager.models import AppSettings, JewelrySKU, MarketplaceListingSKU, Platform, StockBatch
from suppliers.models import Supplier

U = get_user_model()


class Base(TestCase):
    def setUp(self):
        call_command('create_user', 'boss', 'bosspass', '--role', 'master')
        call_command('create_user', 'adam', 'adampass', '--role', 'admin')
        call_command('create_user', 'dina', 'dinapass', '--role', 'operator')
        call_command('create_user', 'ravi', 'ravipass', '--role', 'cashier', '--tiers', 'reselling,wholesale')
        call_command('create_user', 'cost', 'costpass', '--role', 'cashier', '--tiers', 'buying')
        self.boss, self.adam, self.dina, self.ravi, self.cost = (U.objects.get(username=n) for n in ('boss', 'adam', 'dina', 'ravi', 'cost'))
        self.ring = JewelrySKU.objects.create(name='Ring', category='RNG', purchase_price=110, wholesale_price=121, selling_price=140)
        StockBatch.objects.create(sku=self.ring, batch_no='A', quantity=20, purchase_price=110, wholesale_price=121, selling_price=140)
        self.ring.sync_stock_from_batches()

    def sell(self, tier, qty=1, **kw):
        body = dict(items=[{'id': self.ring.pk, 'qty': qty}], price_list=tier, **kw)
        return self.client.post(reverse('pos_checkout'), json.dumps(body), content_type='application/json')


# ------------------------------------------------------------------ separation of the apps
class SeparationTests(Base):
    PATHS = {
        '/': ('admin',), '/inventory/': ('admin',), '/products/': ('admin',), '/analytics/products/': ('admin',),
        '/export/': ('admin',), '/suppliers/': ('admin',), '/import/': ('admin',),
        '/pos/': ('cashier',), '/pos/sales/': ('cashier',), '/pos/api/products/': ('cashier',),
        '/master/': ('master',), '/master/users/': ('master',), '/master/settings/': ('master',), '/master/logs/': ('master',),
        '/scanner/': ('operator',), '/orders/scan-pdf/': ('operator',), '/cancellations/': ('operator',),
        '/orders/history/': ('operator',), '/orders/batch/': ('operator',),
        '/logout/': ('admin', 'master', 'operator', 'cashier'), '/account/password/': ('admin', 'master', 'operator', 'cashier'),
    }

    def test_every_role_can_open_only_its_own_app(self):
        for path, owners in self.PATHS.items():
            match = resolve(path)
            for role in ('master', 'admin', 'operator', 'cashier'):
                self.assertEqual(RoleAccessMiddleware._allowed(role, match) or match.func.__module__.startswith('accounts'),
                                 role in owners, f'{role} on {path}')

    def test_landing_pages(self):
        for name, pw, dest in (('boss', 'bosspass', '/master/'), ('adam', 'adampass', '/'), ('dina', 'dinapass', '/desk/'), ('ravi', 'ravipass', '/pos/')):
            self.client.logout()
            r = self.client.post('/login/', {'username': name, 'password': pw})
            self.assertRedirects(r, dest, fetch_redirect_response=False, msg_prefix=name)

    def test_real_redirects_for_pages_that_render_here(self):
        self.client.force_login(self.adam)
        for url in ('/pos/', '/master/', '/master/settings/', '/desk/'):
            self.assertEqual(self.client.get(url).status_code, 302, url)
        self.client.force_login(self.boss)
        for url in ('/', '/inventory/', '/pos/', '/suppliers/', '/import/', '/desk/'):
            r = self.client.get(url)
            self.assertRedirects(r, '/master/', fetch_redirect_response=False, msg_prefix=url)
        self.client.force_login(self.ravi)
        for url in ('/', '/master/', '/suppliers/', '/desk/'):
            self.assertRedirects(self.client.get(url), '/pos/', fetch_redirect_response=False, msg_prefix=url)
        self.client.force_login(self.dina)
        for url in ('/', '/pos/', '/master/', '/suppliers/', '/import/', '/export/'):
            self.assertRedirects(self.client.get(url), '/desk/', fetch_redirect_response=False, msg_prefix=url)
        self.assertEqual(self.client.get('/desk/').status_code, 200)

    def test_operator_desk_only_for_operator(self):
        for u in (self.adam, self.boss, self.ravi):
            self.client.force_login(u)
            self.assertEqual(self.client.get('/desk/').status_code, 302)

    def test_sidebar_is_role_aware(self):
        self.client.force_login(self.adam)
        html = self.client.get('/suppliers/').content.decode()
        self.assertIn('Suppliers', html); self.assertIn('Import Data', html)
        self.assertNotIn('Fast Scanner', html); self.assertNotIn('PDF Order Scan', html)
        self.assertNotIn('System Settings', html); self.assertNotIn('Master Admin', html)
        self.client.force_login(self.dina)
        html = self.client.get('/desk/').content.decode()
        self.assertIn('Fast Scanner', html); self.assertIn('RTO & Cancellations', html)
        self.assertNotIn('Inventory</span>', html); self.assertNotIn('Suppliers</span>', html)

    def test_anonymous_goes_to_login(self):
        for url in ('/pos/', '/master/', '/suppliers/', '/import/'):
            r = self.client.get(url); self.assertEqual(r.status_code, 302); self.assertIn('/login/', r['Location'])


# ------------------------------------------------------------------ counter prices
class CounterTests(Base):
    def test_reselling_price_is_what_you_entered(self):
        """The bug: Reselling 140 showed as 121 (an invented reseller price)."""
        self.client.force_login(self.ravi)
        prices = self.client.get('/pos/api/products/').json()['products'][0]['prices']
        self.assertEqual(prices, {'reselling': 140.0, 'wholesale': 121.0})
        self.assertEqual(self.sell('reselling').json()['total'], '140.00')
        self.assertEqual(self.sell('wholesale').json()['total'], '121.00')

    def test_only_three_price_lists_and_no_leaks(self):
        self.client.force_login(self.cost)
        body = self.client.get('/pos/api/products/').content.decode()
        self.assertEqual(set(json.loads(body)['products'][0]['prices']), {'buying'})
        self.assertNotIn('140', body); self.assertNotIn('121', body)
        self.assertEqual(self.sell('buying').json()['total'], '110.00')
        self.assertEqual(self.sell('reselling').status_code, 400)
        self.assertEqual(self.sell('buying', discount_percent=5).status_code, 400)

    def test_old_names_from_version_one_still_work(self):
        self.assertEqual(parse_tiers('retail,reseller,wholesale'), ['reselling', 'wholesale'])
        self.client.force_login(self.ravi)
        r = self.sell('retail'); self.assertEqual(r.status_code, 200, r.content); self.assertTrue(r.json()['ok'])   # an old browser tab

    def test_discount_cap_and_stock(self):
        self.client.force_login(self.ravi)
        self.assertEqual(self.sell('reselling', discount_percent=11).status_code, 400)
        self.assertTrue(self.sell('reselling', qty=3, discount_percent=10).json()['ok'])
        self.ring.refresh_from_db(); self.assertEqual(self.ring.stock, 17)

    def test_void_needs_permission_and_only_own_bills(self):
        self.client.force_login(self.ravi); self.sell('reselling', qty=2)
        sale = PosSale.objects.get()
        self.assertEqual(self.client.post(reverse('pos_void', args=[sale.pk])).status_code, 404)
        p = self.ravi.profile; p.can_void = True; p.save()
        self.client.post(reverse('pos_void', args=[sale.pk]))
        sale.refresh_from_db(); self.ring.refresh_from_db()
        self.assertEqual((sale.status, self.ring.stock), ('void', 20))
        self.client.force_login(self.cost); self.cost.profile.can_void = True; self.cost.profile.save()
        self.assertEqual(self.client.post(reverse('pos_void', args=[sale.pk])).status_code, 404)   # not his bill


# ------------------------------------------------------------------ master console
class MasterTests(Base):
    def setUp(self):
        super().setUp(); self.client.force_login(self.boss)

    def test_create_and_edit_everything_about_a_user(self):
        r = self.client.post('/master/users/new/', {'username': 'meena', 'first_name': 'Meena', 'email': 'm@x.com', 'phone': '999',
                             'password': 'secret1', 'password2': 'secret1', 'role': 'cashier', 'tiers': ['wholesale'], 'default_tier': 'wholesale',
                             'max_discount': '5', 'can_void': 'on', 'is_active': 'on'})
        self.assertEqual(r.status_code, 302)
        u = U.objects.get(username='meena')
        self.assertEqual((u.profile.can_void, u.profile.phone, u.email), (True, '999', 'm@x.com'))
        # edit: new username, email, password, role, without a generated password
        self.client.post(f'/master/users/{u.pk}/', {'username': 'meena2', 'first_name': 'Meena S', 'email': 'new@x.com', 'phone': '111',
                         'password': 'mypass99', 'password2': 'mypass99', 'role': 'operator', 'is_active': 'on'})
        u.refresh_from_db()
        self.assertEqual((u.username, u.email, get_role(u), u.is_staff), ('meena2', 'new@x.com', 'operator', False))
        self.assertTrue(u.check_password('mypass99'))
        self.client.logout()
        self.assertRedirects(self.client.post('/login/', {'username': 'meena2', 'password': 'mypass99'}), '/desk/', fetch_redirect_response=False)

    def test_edit_without_password_keeps_it(self):
        self.client.post(f'/master/users/{self.ravi.pk}/', {'username': 'ravi', 'email': 'r@x.com', 'role': 'cashier', 'tiers': ['reselling'], 'is_active': 'on'})
        self.ravi.refresh_from_db(); self.assertTrue(self.ravi.check_password('ravipass')); self.assertEqual(self.ravi.email, 'r@x.com')

    def test_validation(self):
        base = {'username': 'x1', 'password': 'secret1', 'password2': 'secret1', 'role': 'cashier', 'tiers': ['reselling']}
        for bad in (dict(base, username='ravi'), dict(base, username='RAVI'), dict(base, password2='nope'), dict(base, password='abc', password2='abc'),
                    dict(base, tiers=[]), dict(base, max_discount='500'), dict(base, email='not-an-email'), dict(base, username='a b')):
            self.assertEqual(self.client.post('/master/users/new/', bad).status_code, 200, bad)
        self.assertFalse(U.objects.filter(username='x1').exists())
        self.assertEqual(self.client.post(f'/master/users/{self.adam.pk}/', {'username': 'ravi', 'role': 'admin', 'is_active': 'on'}).status_code, 200)

    def test_set_password_is_typed_by_master(self):
        r = self.client.post(f'/master/users/{self.ravi.pk}/password/', {'password': 'mine1234', 'password2': 'mine1234'})
        self.assertEqual(r.status_code, 302)
        self.ravi.refresh_from_db(); self.assertTrue(self.ravi.check_password('mine1234'))
        self.assertEqual(self.client.post(f'/master/users/{self.ravi.pk}/password/', {'password': 'aaaaaa', 'password2': 'bbbbbb'}).status_code, 200)
        self.assertFalse(self.client.get('/master/users/').context is None)
        self.assertFalse(any('reset-password' in a for a in [self.client.get('/master/users/').content.decode()]))

    def test_self_protection(self):
        self.client.post(f'/master/users/{self.boss.pk}/toggle/')
        self.boss.refresh_from_db(); self.assertTrue(self.boss.is_active)
        self.client.post(f'/master/users/{self.boss.pk}/', {'username': 'boss', 'role': 'admin', 'is_active': 'on'})
        self.assertEqual(get_role(U.objects.get(pk=self.boss.pk)), 'master')

    def test_settings_are_master_only(self):
        r = self.client.post('/master/settings/', {'brand_name': 'MY SHOP', 'tagline': 't', 'currency_symbol': '₹', 'low_stock_threshold': '9',
                             'meesho_store_url': 'https://meesho.com/x'})
        self.assertEqual(r.status_code, 302)
        s = AppSettings.get_settings(); self.assertEqual((s.brand_name, s.low_stock_threshold), ('MY SHOP', 9))
        self.assertEqual(self.client.post('/master/settings/', {'brand_name': 'X', 'amazon_store_url': 'notaurl'}).status_code, 200)
        self.assertEqual(AppSettings.get_settings().brand_name, 'MY SHOP')
        self.client.force_login(self.adam)
        self.assertEqual(self.client.post('/master/settings/', {'brand_name': 'HACK'}).status_code, 302)
        self.assertEqual(AppSettings.get_settings().brand_name, 'MY SHOP')
        with self.assertRaises(Exception):
            reverse('app_settings_old')

    def test_read_only_data_pages(self):
        self.client.force_login(self.ravi); self.sell('reselling', qty=2)
        self.client.force_login(self.boss)
        sale = PosSale.objects.get()
        for url in ('/master/', '/master/sales/', f'/master/sales/{sale.pk}/', '/master/stock/', '/master/logs/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get('/master/sales/'), sale.bill_no)
        self.assertContains(self.client.get('/master/stock/'), 'Ring')

    def test_audit(self):
        self.client.logout()
        self.client.post('/login/', {'username': 'ravi', 'password': 'bad'})
        self.client.post('/login/', {'username': 'ravi', 'password': 'ravipass'})
        self.sell('reselling')
        acts = list(AuditLog.objects.order_by('pk').values_list('action', flat=True))
        self.assertEqual(acts, ['login_failed', 'login', 'sale'])
        self.client.force_login(self.boss)
        self.client.post(f'/master/users/{self.ravi.pk}/password/', {'password': 'topsecret', 'password2': 'topsecret'})
        self.assertNotIn('topsecret', ' '.join(f'{a.detail}{a.path}' for a in AuditLog.objects.all()))


# ------------------------------------------------------------------ suppliers + stock form
class SupplierTests(Base):
    def setUp(self):
        super().setUp(); self.client.force_login(self.adam)

    def test_crud(self):
        r = self.client.post('/suppliers/new/', {'name': 'Shree  Traders', 'phone': '98', 'city': 'Surat', 'gstin': '24abc', 'is_active': 'on'})
        self.assertEqual(r.status_code, 302)
        s = Supplier.objects.get(); self.assertEqual((s.name, s.gstin), ('Shree Traders', '24ABC'))
        self.assertEqual(self.client.post('/suppliers/new/', {'name': 'shree traders'}).status_code, 200)       # duplicate
        self.client.post(f'/suppliers/{s.pk}/edit/', {'name': 'Shree Traders', 'city': 'Rajkot', 'is_active': 'on'})
        s.refresh_from_db(); self.assertEqual(s.city, 'Rajkot')
        self.assertContains(self.client.get('/suppliers/?q=raj'), 'Shree Traders')
        self.assertNotContains(self.client.get('/suppliers/?q=zzz'), 'Shree Traders')
        self.client.post(f'/suppliers/{s.pk}/toggle/'); s.refresh_from_db(); self.assertFalse(s.is_active)
        self.client.post(f'/suppliers/{s.pk}/delete/'); self.assertFalse(Supplier.objects.exists())

    def test_cannot_delete_supplier_with_batches(self):
        s = Supplier.objects.create(name='S1'); StockBatch.objects.filter().update(supplier=s)
        self.client.post(f'/suppliers/{s.pk}/delete/'); self.assertTrue(Supplier.objects.exists())
        self.assertContains(self.client.get(f'/suppliers/{s.pk}/'), 'Ring')

    def test_search_and_quick_add_api(self):
        Supplier.objects.create(name='Shree Traders', phone='9876', city='Surat'); Supplier.objects.create(name='Mahalaxmi', is_active=False)
        j = self.client.get('/suppliers/api/search/?q=shr').json()
        self.assertEqual([r['name'] for r in j['results']], ['Shree Traders']); self.assertFalse(j['exact'])
        self.assertEqual(len(self.client.get('/suppliers/api/search/?q=9876').json()['results']), 1)
        self.assertTrue(self.client.get('/suppliers/api/search/?q=shree traders').json()['exact'])
        self.assertEqual(self.client.get('/suppliers/api/search/?q=maha').json()['results'], [])          # disabled hidden
        r = self.client.post('/suppliers/api/quick-add/', json.dumps({'name': ' New  Vendor '}), content_type='application/json').json()
        self.assertEqual((r['created'], r['name']), (True, 'New Vendor'))
        r2 = self.client.post('/suppliers/api/quick-add/', json.dumps({'name': 'new vendor'}), content_type='application/json').json()
        self.assertEqual((r2['created'], r2['id']), (False, r['id']))
        self.assertEqual(self.client.post('/suppliers/api/quick-add/', json.dumps({'name': ' '}), content_type='application/json').status_code, 400)

    def test_other_roles_locked_out(self):
        for u in (self.ravi, self.dina, self.boss):
            self.client.force_login(u)
            self.assertEqual(self.client.get('/suppliers/api/search/').status_code, 302)

    def test_link_suppliers_command(self):
        StockBatch.objects.all().update(supplier_name='Shree Traders')
        StockBatch.objects.create(sku=self.ring, batch_no='B', quantity=1, supplier_name='Primary Supplier')
        call_command('link_suppliers')
        self.assertEqual(list(Supplier.objects.values_list('name', flat=True)), ['Shree Traders'])
        self.assertEqual(StockBatch.objects.filter(supplier__isnull=False).count(), 1)


class StockFormTests(Base):
    def setUp(self):
        super().setUp(); self.client.force_login(self.adam); self.url = reverse('stock_action', args=[self.ring.pk])
        self.sup = Supplier.objects.create(name='Shree Traders')

    def post(self, **kw):
        data = {'batch_no': 'B-100', 'stock_qty': '12', 'purchase_price': '100', 'wholesale_price': '120', 'reselling_price': '150', 'supplier_id': str(self.sup.pk)}
        data.update(kw)
        return self.client.post(self.url, {k: v for k, v in data.items() if v is not None})

    def test_form_has_no_auto_batch_name_or_quantity(self):
        html = self.client.get(self.url).content.decode()
        self.assertNotIn('BAT-', html)
        import re
        batch_input = re.search(r'<input[^>]*name="batch_no"[^>]*>', html, re.S).group(0)
        qty_input = re.search(r'<input[^>]*name="stock_qty"[^>]*>', html, re.S).group(0)
        self.assertNotRegex(batch_input, r'value="[^"]+"'); self.assertNotRegex(qty_input, r'value="[^"]+"')
        self.assertIn('sup-picker', html)

    def test_batch_and_quantity_are_required(self):
        n = self.ring.batches.count()
        for kw in (dict(batch_no=''), dict(batch_no='   '), dict(stock_qty=''), dict(stock_qty='abc'), dict(stock_qty='0'), dict(stock_qty='-3'), dict(batch_no='a')):
            r = self.post(**kw)
            self.assertEqual(r.status_code, 200, kw)          # re-shown with the problem, nothing saved
            self.assertContains(r, 'fa-circle-exclamation')
        self.assertEqual(self.ring.batches.count(), n)

    def test_duplicate_batch_number_refused(self):
        self.assertEqual(self.post(batch_no='B-100').status_code, 302)
        self.assertContains(self.post(batch_no='b-100'), 'already exists')
        self.assertEqual(self.ring.batches.filter(batch_no__iexact='b-100').count(), 1)

    def test_batch_is_saved_with_its_supplier(self):
        self.assertEqual(self.post().status_code, 302)
        b = self.ring.batches.get(batch_no='B-100')
        self.assertEqual((b.quantity, b.supplier_id, b.supplier_name, b.selling_price), (12, self.sup.pk, 'Shree Traders', D('150')))
        self.ring.refresh_from_db(); self.assertEqual(self.ring.stock, 32)

    def test_different_batches_different_suppliers(self):
        s2 = Supplier.objects.create(name='Mahalaxmi')
        self.post(batch_no='B-1'); self.post(batch_no='B-2', supplier_id=str(s2.pk)); self.post(batch_no='B-3', supplier_id='')
        got = {b.batch_no: b.supplier_name for b in self.ring.batches.exclude(batch_no='A')}
        self.assertEqual(got, {'B-1': 'Shree Traders', 'B-2': 'Mahalaxmi', 'B-3': ''})

    def test_edit_batch_changes_supplier_and_rejects_unknown_one(self):
        self.post(); b = self.ring.batches.get(batch_no='B-100'); s2 = Supplier.objects.create(name='Other')
        edit = self.url + f'?edit_batch={b.pk}'
        self.client.post(edit, {'batch_no': 'B-100', 'stock_qty': '9', 'supplier_id': str(s2.pk)})
        b.refresh_from_db(); self.assertEqual((b.supplier_id, b.quantity), (s2.pk, 9))
        self.assertContains(self.client.post(edit, {'batch_no': 'B-100', 'stock_qty': '9', 'supplier_id': '9999'}), 'no longer exists')
        self.assertContains(self.client.get(edit), 'value="Other"')


# ------------------------------------------------------------------ import
def csv_file(text, name='data.csv'):
    return SimpleUploadedFile(name, text.encode('utf-8'), content_type='text/csv')


class ImportTests(Base):
    def setUp(self):
        super().setUp(); self.client.force_login(self.adam)
        self.meesho, _ = Platform.objects.get_or_create(name='Meesho'); Platform.objects.get_or_create(name='Amazon')

    def upload(self, kind, text, name='data.csv'):
        r = self.client.post(f'/import/new/{kind}/', {'file': csv_file(text, name)})
        self.assertEqual(r.status_code, 302, r.content[:300])
        return ImportJob.objects.latest('pk')

    def run_import(self, kind, text, mapping=None, extra=None, name='data.csv'):
        job = self.upload(kind, text, name)
        page = self.client.get(f'/import/job/{job.pk}/map/')
        self.assertEqual(page.status_code, 200)
        if mapping is None:                                    # accept the automatic guess
            from importer import engine
            headers, _ = engine.load(job)
            mapping = {k: str(v['col']) for k, v in engine.guess_mapping(kind, headers).items()}
        data = {f'map_{k}': v for k, v in mapping.items()}
        data.update(extra or {})
        r = self.client.post(f'/import/job/{job.pk}/map/', data)
        self.assertEqual(r.status_code, 302, r.content.decode()[:2000] if r.status_code == 200 else '')
        pv = self.client.get(f'/import/job/{job.pk}/preview/'); self.assertEqual(pv.status_code, 200)
        self.client.post(f'/import/job/{job.pk}/commit/')
        job.refresh_from_db(); return job

    # --- products
    def test_products_get_automatic_sku_and_serial(self):
        JewelrySKU.objects.create(name='Old', category='BGL')      # SKU ends -001
        before = JewelrySKU.objects.count()
        job = self.run_import('products', 'Product Name,Category,Colour,Size,Meesho seller SKU\n'
                                          'Gold Bangle Set,Bangles,Gold,2.6,MSH-1\nHeart Necklace,NCK,Silver,Free,\nPearl Ring,Rings,,,\n')
        self.assertEqual(job.result['counts'], {'created': 3, 'updated': 0, 'skipped': 0, 'errors': 0})
        new = list(JewelrySKU.objects.order_by('-pk')[:3][::-1])
        self.assertEqual([p.sku for p in new], ['BGL-STD-GOLDBANGLESET-GOLD-26-002', 'NCK-STD-HEARTNECKLACE-SILVER-FREE-003', 'RNG-STD-PEARLRING-BLK-FREE-004'])
        m = MarketplaceListingSKU.objects.get(sku=new[0], platform=self.meesho)
        self.assertEqual((m.source, m.listing_sku), ('manual', 'MSH-1'))
        self.assertEqual(MarketplaceListingSKU.objects.filter(sku=new[1]).count(), 2)       # a row per marketplace like the form
        self.assertEqual(JewelrySKU.objects.count(), before + 3)

    def test_explicit_serial_and_fixed_value(self):
        job = self.run_import('products', 'Name,Serial\nAnklet A,7\nAnklet B,\n', mapping={'name': '0', 'number': '1'}, extra={'map_category': '__fixed__', 'fixed_category': 'Anklets'})
        self.assertEqual(job.result['counts']['created'], 2)
        skus = set(JewelrySKU.objects.exclude(pk=self.ring.pk).values_list('sku', flat=True))
        self.assertIn('PAY-STD-ANKLETA-BLK-FREE-007', skus)

    def test_importing_the_same_file_twice_skips(self):
        text = 'Product,Category\nBangle X,BGL\nBangle Y,BGL\n'
        self.run_import('products', text)
        job = self.run_import('products', text)
        self.assertEqual(job.result['counts'], {'created': 0, 'updated': 0, 'skipped': 2, 'errors': 0})
        self.assertEqual(JewelrySKU.objects.filter(name='BANGLE X').count(), 1)

    def test_update_duplicates_option(self):
        self.run_import('products', 'Product,Category,Design\nBangle X,BGL,D1\n')
        job = self.run_import('products', 'Product,Category,Design\nBangle X,BGL,D2\n', extra={'on_duplicate': 'update'})
        self.assertEqual(job.result['counts']['updated'], 1)
        self.assertEqual(JewelrySKU.objects.get(name='BANGLE X').design_model, 'D2')

    def test_bad_rows_reported_and_good_rows_kept(self):
        job = self.run_import('products', 'Product,Category\nGood One,BGL\n,BGL\nBad Cat,Spaceship\nGood Two,Ring\n')
        self.assertEqual(job.result['counts'], {'created': 2, 'updated': 0, 'skipped': 0, 'errors': 2})
        msgs = [e['message'] for e in job.result['errors']]
        self.assertTrue(any('empty' in m for m in msgs)); self.assertTrue(any('Spaceship' in m for m in msgs))
        csv_resp = self.client.get(f'/import/job/{job.pk}/errors.csv')
        self.assertIn('Spaceship', csv_resp.content.decode()); self.assertIn('attachment', csv_resp['Content-Disposition'])

    def test_all_or_nothing(self):
        job = self.run_import('products', 'Product,Category\nGood One,BGL\nBad,Nope\n', extra={'strict': 'on'})
        self.assertEqual(job.status, 'failed'); self.assertFalse(JewelrySKU.objects.filter(name='GOOD ONE').exists())

    def test_preview_saves_nothing(self):
        job = self.upload('products', 'Product,Category\nGhost,BGL\n')
        self.client.post(f'/import/job/{job.pk}/map/', {'map_name': '0', 'map_category': '1'})
        self.client.get(f'/import/job/{job.pk}/preview/')
        self.assertFalse(JewelrySKU.objects.filter(name='GHOST').exists())

    def test_required_fields_must_be_mapped(self):
        job = self.upload('products', 'Product,Category\nA,BGL\n')
        r = self.client.post(f'/import/job/{job.pk}/map/', {'map_name': '0'})
        self.assertEqual(r.status_code, 200); self.assertContains(r, 'Map these required fields')

    def test_saved_mapping_is_reused_on_a_new_file(self):
        job = self.upload('products', 'Item,Kind\nA,BGL\n')
        self.client.post(f'/import/job/{job.pk}/map/', {'map_name': '0', 'map_category': '1', 'save_as': 'my sheet'})
        job2 = self.upload('products', 'Kind,Item\nBGL,B\n')
        from importer.models import ImportMapping
        pk = ImportMapping.objects.get().pk
        html = self.client.get(f'/import/job/{job2.pk}/map/?use={pk}').content.decode()
        self.assertIn('<option value="1" selected>Item', html); self.assertIn('<option value="0" selected>Kind', html)

    # --- inventory
    def test_inventory_batches_with_suppliers(self):
        job = self.run_import('inventory', 'SKU ID,Batch number,Qty,Buying price,Reselling price,Supplier\n'
                                           f'{self.ring.sku},B-1,10,100,150,Shree Traders\n{self.ring.sku},B-2,5,,,Shree Traders\n',
                              extra={'create_suppliers': 'on'})
        self.assertEqual(job.result['counts']['created'], 2)
        b1, b2 = self.ring.batches.get(batch_no='B-1'), self.ring.batches.get(batch_no='B-2')
        self.assertEqual((b1.quantity, b1.purchase_price, b1.selling_price, b1.supplier.name), (10, D('100'), D('150'), 'Shree Traders'))
        self.assertEqual(b2.supplier_id, b1.supplier_id); self.assertEqual(Supplier.objects.count(), 1)
        self.ring.refresh_from_db(); self.assertEqual(self.ring.stock, 35)

    def test_inventory_never_invents_batch_or_quantity(self):
        job = self.run_import('inventory', f'SKU ID,Batch number,Qty\n{self.ring.sku},,5\n{self.ring.sku},B-9,\n{self.ring.sku},B-8,0\n{self.ring.sku},B-7,abc\n')
        self.assertEqual(job.result['counts'], {'created': 0, 'updated': 0, 'skipped': 0, 'errors': 4})
        self.assertEqual(self.ring.batches.count(), 1)

    def test_inventory_matching_rules(self):
        twin = JewelrySKU.objects.create(name='Ring', category='RNG', color='Red')
        job = self.run_import('inventory', 'Product,Batch,Qty\nRing,X1,1\nNothing,X2,1\n')
        msgs = ' | '.join(e['message'] for e in job.result['errors'])
        self.assertIn('2 products are named', msgs); self.assertIn('No product named', msgs)
        job = self.run_import('inventory', f'SKU ID,Batch,Qty\n{twin.sku.lower()},X3,2\n{self.ring.sku},A,3\n')
        self.assertEqual(job.result['counts'], {'created': 1, 'updated': 0, 'skipped': 1, 'errors': 0})   # existing batch A skipped

    def test_inventory_unknown_supplier_needs_the_option(self):
        text = f'SKU ID,Batch,Qty,Supplier\n{self.ring.sku},S1,2,Nobody Ltd\n'
        job = self.run_import('inventory', text)
        self.assertEqual(job.result['counts']['errors'], 1); self.assertFalse(Supplier.objects.exists())

    # --- suppliers
    def test_suppliers_import(self):
        job = self.run_import('suppliers', 'Supplier,Mobile,City,GST\nShree Traders,98765,Surat,24abc\nshree traders,1,x,\nMahalaxmi,,Rajkot,\n')
        self.assertEqual(job.result['counts'], {'created': 2, 'updated': 0, 'skipped': 1, 'errors': 0})
        self.assertEqual(Supplier.objects.get(name='Shree Traders').gstin, '24ABC')

    # --- files
    def test_excel_file(self):
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(['Product', 'Category', 'Serial']); ws.append(['Excel Ring', 'RNG', 12]); ws.append(['Excel Chain', 'NCK', 13.0])
        buf = io.BytesIO(); wb.save(buf)
        r = self.client.post('/import/new/products/', {'file': SimpleUploadedFile('p.xlsx', buf.getvalue())})
        job = ImportJob.objects.latest('pk')
        self.client.post(f'/import/job/{job.pk}/map/', {'map_name': '0', 'map_category': '1', 'map_number': '2'})
        self.client.post(f'/import/job/{job.pk}/commit/')
        self.assertTrue(JewelrySKU.objects.filter(sku='RNG-STD-EXCELRING-BLK-FREE-012').exists())
        self.assertTrue(JewelrySKU.objects.filter(sku='NCK-STD-EXCELCHAIN-BLK-FREE-013').exists())

    def test_bad_files_are_refused(self):
        for f in (csv_file('', 'e.csv'), csv_file('Product,Category\n', 'h.csv'), csv_file('a', 'x.pdf')):
            r = self.client.post('/import/new/products/', {'file': f}); self.assertEqual(r.status_code, 200)
        self.assertEqual(ImportJob.objects.count(), 0)

    def test_semicolon_and_cp1252_csv(self):
        raw = 'Product;Category\nKangan é;BGL\n'.encode('cp1252')
        self.client.post('/import/new/products/', {'file': SimpleUploadedFile('s.csv', raw)})
        job = ImportJob.objects.latest('pk')
        self.client.post(f'/import/job/{job.pk}/map/', {'map_name': '0', 'map_category': '1'}); self.client.post(f'/import/job/{job.pk}/commit/')
        self.assertTrue(JewelrySKU.objects.filter(name='KANGAN É').exists())

    def test_import_is_logged_and_center_pages_load(self):
        self.run_import('suppliers', 'Supplier\nA\n')
        self.assertTrue(AuditLog.objects.filter(action='import').exists())
        for url in ('/import/', '/import/sample/products/', '/import/sample/inventory/', '/import/sample/suppliers/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get('/import/new/bogus/').status_code, 404)

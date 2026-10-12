from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from polars import Decimal
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
import tempfile
import qrcode

from .models import (
	AppSettings,
	DispatchLog,
	JewelrySKU,
	MarketplaceListingSKU,
	Platform,
	PlatformPrice,
	StockBatch,
	StorageBox,
)
from .views import COLOR_PALETTE


class SalesHoldTests(TestCase):
	def setUp(self):
		self.item = JewelrySKU.objects.create(name='Hold Test Product')
		self.platform = Platform.objects.create(name='Test Platform')

	def test_stock_sync_excludes_batches_on_hold(self):
		StockBatch.objects.create(
			sku=self.item,
			batch_no='HOLD-1',
			quantity=4,
			sales_enabled=False,
		)
		StockBatch.objects.create(
			sku=self.item,
			batch_no='SELL-1',
			quantity=2,
		)

		self.item.sync_stock_from_batches()

		self.item.refresh_from_db()
		self.assertEqual(self.item.stock, 2)

	def test_dispatch_skips_older_batch_on_hold(self):
		held_batch = StockBatch.objects.create(
			sku=self.item,
			batch_no='HOLD-1',
			quantity=4,
			sales_enabled=False,
		)
		sellable_batch = StockBatch.objects.create(
			sku=self.item,
			batch_no='SELL-1',
			quantity=2,
		)

		response = self.client.post(
			reverse('scan_dispatch', args=[self.item.sku]),
			{'platform_id': self.platform.pk},
		)

		held_batch.refresh_from_db()
		sellable_batch.refresh_from_db()
		self.item.refresh_from_db()
		self.assertEqual(response.status_code, 302)
		self.assertEqual(held_batch.quantity, 4)
		self.assertEqual(sellable_batch.quantity, 1)
		self.assertEqual(self.item.stock, 1)
		self.assertEqual(DispatchLog.objects.filter(sku=self.item).count(), 1)

	def test_toggle_sales_updates_sellable_stock(self):
		user = get_user_model().objects.create_user(username='stock-user', password='test-password')
		batch = StockBatch.objects.create(
			sku=self.item,
			batch_no='HOLD-1',
			quantity=4,
		)
		self.client.force_login(user)

		response = self.client.post(reverse('toggle_batch_sales', args=[batch.pk]))

		batch.refresh_from_db()
		self.item.refresh_from_db()
		self.assertEqual(response.status_code, 302)
		self.assertFalse(batch.sales_enabled)
		self.assertEqual(self.item.stock, 0)

		self.client.post(reverse('toggle_batch_sales', args=[batch.pk]))

		batch.refresh_from_db()
		self.item.refresh_from_db()
		self.assertTrue(batch.sales_enabled)
		self.assertEqual(self.item.stock, 4)


class BoxManagerColorTests(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username='box-user', password='test-password')
		self.client.force_login(user)
		self.url = reverse('box_manager')

	def test_palette_contains_72_unique_swatches_and_marks_used_colors(self):
		used_color = COLOR_PALETTE[0]
		StorageBox.objects.create(name='Existing Box', color_tag=used_color)

		response = self.client.get(self.url)

		self.assertEqual(len(COLOR_PALETTE), 72)
		self.assertEqual(len(set(COLOR_PALETTE)), 72)
		swatches = response.context['color_swatches']
		self.assertEqual(len(swatches), 72)
		used_swatch = next(swatch for swatch in swatches if swatch['hex'] == used_color)
		self.assertTrue(used_swatch['is_taken'])
		self.assertEqual(used_swatch['used_by'], 'Existing Box')

	def test_creates_box_with_valid_custom_color(self):
		response = self.client.post(self.url, {
			'box_name': 'Custom Box',
			'color_tag': '#123456',
			'description': 'Test location',
		})

		self.assertRedirects(response, self.url)
		self.assertTrue(StorageBox.objects.filter(name='Custom Box', color_tag='#123456').exists())

	def test_rejects_invalid_color_duplicate_name_and_duplicate_color(self):
		StorageBox.objects.create(name='Existing Box', color_tag='#A1B2C3')
		invalid_submissions = [
			{'box_name': 'Invalid Color', 'color_tag': 'not-a-color'},
			{'box_name': 'existing box', 'color_tag': '#123456'},
			{'box_name': 'Duplicate Color', 'color_tag': '#A1B2C3'},
		]

		for submission in invalid_submissions:
			with self.subTest(submission=submission):
				response = self.client.post(self.url, submission)
				self.assertRedirects(response, self.url)

		self.assertEqual(StorageBox.objects.count(), 1)


class ProductDetailTests(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username='detail-user', password='test-password')
		self.client.force_login(user)
		self.item = JewelrySKU.objects.create(name='Detail Test Product')
		self.platform = Platform.objects.create(name='Detail Platform')
		self.url = reverse('product_detail', args=[self.item.pk])

	def test_detail_page_shows_batch_stock_and_sales_metrics(self):
		StockBatch.objects.create(
			sku=self.item,
			batch_no='SELL-DETAIL',
			quantity=2,
			purchase_price='5.00',
			selling_price='10.00',
		)
		StockBatch.objects.create(
			sku=self.item,
			batch_no='HOLD-DETAIL',
			quantity=3,
			purchase_price='6.00',
			selling_price='12.00',
			sales_enabled=False,
		)
		PlatformPrice.objects.create(sku=self.item, platform=self.platform, price='8.00')
		DispatchLog.objects.create(
			sku=self.item,
			platform=self.platform,
			platform_name=self.platform.name,
			quantity=1,
			sold_price='9.00',
			stock_after=1,
		)

		response = self.client.get(self.url)

		self.assertEqual(response.status_code, 200)
		self.assertTemplateUsed(response, 'sku_manager/product_detail.html')
		self.assertEqual(response.context['sellable_units'], 2)
		self.assertEqual(response.context['held_units'], 3)
		self.assertEqual(float(response.context['cost_value']), 10.0)
		self.assertEqual(float(response.context['retail_value']), 20.0)
		self.assertEqual(response.context['total_units_sold'], 1)
		self.assertEqual(float(response.context['total_revenue']), 9.0)
		self.assertEqual(response.context['units_30d'], 1)
		platform_sales = response.context['by_platform'][0]
		self.assertEqual(platform_sales['first_sale'], response.context['last_dispatch'].dispatched_at)
		self.assertEqual(platform_sales['last_sale'], response.context['last_dispatch'].dispatched_at)
		self.assertContains(response, 'On Hold')

	def test_detail_page_shows_marketplace_listing_sku(self):
		MarketplaceListingSKU.objects.create(
			sku=self.item,
			platform=self.platform,
			listing_sku='AMZ-DETAIL-123',
		)

		response = self.client.get(self.url)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'AMZ-DETAIL-123')

	def test_list_status_toggle_returns_to_safe_next_path(self):
		response = self.client.post(
			reverse('toggle_product_status', args=[self.item.pk]),
			{'next': self.url},
		)

		self.assertRedirects(response, self.url)
		self.item.refresh_from_db()
		self.assertFalse(self.item.is_listed)

	def test_list_status_toggle_rejects_external_next_path(self):
		response = self.client.post(
			reverse('toggle_product_status', args=[self.item.pk]),
			{'next': '//example.com/unsafe'},
		)

		self.assertRedirects(response, reverse('product_batches', args=[self.item.pk]))


class MarketplaceListingSKUTests(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username='mapping-user', password='test-password')
		self.client.force_login(user)
		self.item = JewelrySKU.objects.create(name='Mapping Test Product')
		self.platform = Platform.objects.create(name='Amazon')
		self.url = reverse('stock_action', args=[self.item.pk])

	def test_stock_form_saves_marketplace_sku_mapping(self):
		response = self.client.post(self.url, {
			'batch_no': 'MAP-1',
			'stock_qty': '5',
			'purchase_price': '10.00',
			'selling_price': '20.00',
			f'listing_sku_source_{self.platform.pk}': MarketplaceListingSKU.SOURCE_MANUAL,
			f'listing_sku_{self.platform.pk}': 'AMZ-MAPPING-123',
		})

		self.assertRedirects(response, reverse('inventory_list'))
		mapping = MarketplaceListingSKU.objects.get(sku=self.item, platform=self.platform)
		self.assertEqual(mapping.listing_sku, 'AMZ-MAPPING-123')
		self.assertEqual(mapping.effective_sku, 'AMZ-MAPPING-123')
		self.assertEqual(self.item.batches.get().quantity, 5)

	def test_database_sku_mode_uses_the_current_product_sku(self):
		response = self.client.post(self.url, {
			'batch_no': 'MAP-DATABASE',
			'stock_qty': '3',
			'purchase_price': '10.00',
			'selling_price': '20.00',
			f'listing_sku_source_{self.platform.pk}': MarketplaceListingSKU.SOURCE_DATABASE,
		})

		self.assertRedirects(response, reverse('inventory_list'))
		mapping = MarketplaceListingSKU.objects.get(sku=self.item, platform=self.platform)
		self.assertEqual(mapping.source, MarketplaceListingSKU.SOURCE_DATABASE)
		self.assertEqual(mapping.listing_sku, '')
		self.assertEqual(mapping.effective_sku, self.item.sku)

		self.item.name = 'Updated Mapping Product'
		self.item.save()
		mapping.refresh_from_db()
		self.assertEqual(mapping.effective_sku, self.item.sku)

	def test_get_form_defaults_to_database_sku_mode(self):
		response = self.client.get(self.url)

		self.assertEqual(response.status_code, 200)
		row = next(row for row in response.context['platform_rows'] if row['platform'] == self.platform)
		self.assertEqual(row['listing_sku_source'], MarketplaceListingSKU.SOURCE_DATABASE)
		self.assertEqual(row['listing_sku'], '')

	def test_duplicate_marketplace_sku_is_rejected_without_saving_stock(self):
		other_item = JewelrySKU.objects.create(name='Other Mapping Product')
		MarketplaceListingSKU.objects.create(
			sku=other_item,
			platform=self.platform,
			listing_sku='AMZ-ALREADY-USED',
		)

		response = self.client.post(self.url, {
			'batch_no': 'MAP-DUPLICATE',
			'stock_qty': '5',
			'purchase_price': '10.00',
			'selling_price': '20.00',
			f'listing_sku_source_{self.platform.pk}': MarketplaceListingSKU.SOURCE_MANUAL,
			f'listing_sku_{self.platform.pk}': 'amz-already-used',
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'already mapped to another product')
		self.assertFalse(self.item.batches.exists())
		self.assertFalse(MarketplaceListingSKU.objects.filter(sku=self.item).exists())

	def test_switching_seller_sku_to_database_uses_product_sku(self):
		MarketplaceListingSKU.objects.create(
			sku=self.item,
			platform=self.platform,
			listing_sku='AMZ-OLD-SKU',
		)

		response = self.client.post(self.url, {
			'batch_no': 'MAP-CLEAR',
			'stock_qty': '5',
			'purchase_price': '10.00',
			'selling_price': '20.00',
			f'listing_sku_source_{self.platform.pk}': MarketplaceListingSKU.SOURCE_DATABASE,
			f'listing_sku_{self.platform.pk}': 'AMZ-OLD-SKU',
		})

		self.assertRedirects(response, reverse('inventory_list'))
		mapping = MarketplaceListingSKU.objects.get(sku=self.item, platform=self.platform)
		self.assertEqual(mapping.source, MarketplaceListingSKU.SOURCE_DATABASE)
		self.assertEqual(mapping.effective_sku, self.item.sku)

	def test_manual_mode_requires_a_seller_sku(self):
		response = self.client.post(self.url, {
			'batch_no': 'MAP-MISSING',
			'stock_qty': '5',
			'purchase_price': '10.00',
			'selling_price': '20.00',
			f'listing_sku_source_{self.platform.pk}': MarketplaceListingSKU.SOURCE_MANUAL,
			f'listing_sku_{self.platform.pk}': '',
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Enter a seller SKU for Amazon')
		self.assertFalse(self.item.batches.exists())


class LabelPrintingTests(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username='label-user', password='test-password')
		self.client.force_login(user)
		self.item = JewelrySKU.objects.create(name='Label Test Product')
		self.older_batch = StockBatch.objects.create(
			sku=self.item,
			batch_no='OLD-LABEL',
			quantity=4,
		)
		self.latest_batch = StockBatch.objects.create(
			sku=self.item,
			batch_no='NEW-LABEL',
			quantity=2,
			section_name='Tray 7',
		)
		self.url = reverse('sku_print_label', args=[self.item.pk])

	def test_label_page_defaults_to_latest_batch_and_renders_copies(self):
		response = self.client.get(self.url)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['batch'], self.latest_batch)
		self.assertEqual(response.context['copies'], self.latest_batch.quantity)
		self.assertEqual(response.context['rows'], [[1, 1]])
		self.assertContains(response, self.item.name)
		self.assertContains(response, self.item.sku)
		self.assertContains(response, 'NEW-LABEL')
		self.assertContains(response, 'data:image/png;base64,')
		inventory_url = reverse('inventory_list')
		self.assertContains(response, f'href="{inventory_url}"')

	def test_label_qr_encodes_only_the_sku(self):
		payloads = []
		original_add_data = qrcode.QRCode.add_data

		def capture_payload(qr, data, *args, **kwargs):
			payloads.append(data)
			return original_add_data(qr, data, *args, **kwargs)

		with patch('sku_manager.views.qrcode.QRCode.add_data', autospec=True, side_effect=capture_payload):
			label_response = self.client.get(self.url)

		self.assertEqual(label_response.status_code, 200)
		self.assertEqual(payloads, [self.item.sku])
		self.assertEqual(label_response.context['qr_payload'], self.item.sku)

	def test_inventory_has_print_label_action_for_each_sku(self):
		response = self.client.get(reverse('inventory_list'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, reverse('sku_print_label', args=[self.item.pk]))
		self.assertContains(response, '>🏷️ Label</a>')

	def test_label_page_falls_back_for_unknown_batch_and_clamps_copies(self):
		response = self.client.get(self.url, {'batch': '999999', 'copies': '900'})

		self.assertEqual(response.context['batch'], self.latest_batch)
		self.assertEqual(response.context['copies'], 500)
		self.assertEqual(len(response.context['rows']), 250)
		self.assertTrue(all(row == [1, 1] for row in response.context['rows']))

	def test_save_and_print_redirects_to_new_batch_labels(self):
		response = self.client.post(reverse('stock_action', args=[self.item.pk]), {
			'batch_no': 'PRINT-ME',
			'stock_qty': '6',
			'purchase_price': '4.50',
			'selling_price': '9.00',
			'existing_box_id': '',
			'section_name': 'Tray 2',
			'then': 'label',
		})

		created_batch = StockBatch.objects.get(sku=self.item, batch_no='PRINT-ME')
		self.assertRedirects(
			response,
			f"{self.url}?batch={created_batch.pk}&copies=6",
			fetch_redirect_response=False,
		)

	@override_settings(MEDIA_ROOT=tempfile.gettempdir())
	def test_settings_upload_logo_and_label_uses_it(self):
		logo = SimpleUploadedFile('label-logo.png', b'fake-image-data', content_type='image/png')
		response = self.client.post(reverse('app_settings'), {
			'brand_name': 'TEST BRAND',
			'tagline': '',
			'currency_symbol': '$',
			'low_stock_threshold': '5',
			'support_contact': '',
			'label_logo': logo,
		})

		self.assertRedirects(response, reverse('app_settings'))
		settings_obj = AppSettings.get_settings()
		self.assertTrue(settings_obj.label_logo.name.startswith('branding/'))
		self.assertTrue(settings_obj.label_logo.name.endswith('.png'))

		label_response = self.client.get(self.url)
		self.assertEqual(label_response.status_code, 200)
		self.assertEqual(label_response.context['label_logo_url'], settings_obj.label_logo.url)
		self.assertContains(label_response, settings_obj.label_logo.url)

	@override_settings(MEDIA_ROOT=tempfile.gettempdir())
	def test_settings_can_remove_uploaded_label_logo(self):
		settings_obj = AppSettings.get_settings()
		settings_obj.label_logo.save(
			'existing-logo.png',
			SimpleUploadedFile('existing-logo.png', b'fake-image-data', content_type='image/png'),
		)

		self.client.post(reverse('app_settings'), {
			'brand_name': settings_obj.brand_name,
			'tagline': settings_obj.tagline,
			'currency_symbol': settings_obj.currency_symbol,
			'low_stock_threshold': settings_obj.low_stock_threshold,
			'support_contact': settings_obj.support_contact,
			'remove_label_logo': '1',
		})

		settings_obj.refresh_from_db()
		self.assertFalse(settings_obj.label_logo)

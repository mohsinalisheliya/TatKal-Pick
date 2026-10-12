from django.contrib import admin
from .models import JewelrySKU, MarketplaceListingSKU, Platform, PlatformPrice, DispatchLog


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'created_at')
    search_fields = ('name',)
    list_per_page = 25


@admin.register(JewelrySKU)
class JewelrySKUAdmin(admin.ModelAdmin):
    list_display = ('sku', 'name', 'category', 'color', 'stock', 'is_listed', 'selling_price', 'created_at')
    list_filter = ('is_listed', 'category', 'color')
    search_fields = ('sku', 'name', 'style', 'category', 'color')
    ordering = ('-created_at',)
    list_per_page = 25


@admin.register(PlatformPrice)
class PlatformPriceAdmin(admin.ModelAdmin):
    list_display = ('sku', 'platform', 'price')
    list_filter = ('platform',)
    search_fields = ('sku__sku', 'platform__name')
    list_per_page = 25


@admin.register(MarketplaceListingSKU)
class MarketplaceListingSKUAdmin(admin.ModelAdmin):
    list_display = ('platform', 'listing_sku', 'sku')
    list_filter = ('platform',)
    search_fields = ('listing_sku', 'sku__sku', 'sku__name', 'platform__name')
    list_per_page = 25


@admin.register(DispatchLog)
class DispatchLogAdmin(admin.ModelAdmin):
    list_display = ('sku', 'platform_name', 'quantity', 'sold_price', 'stock_after', 'dispatched_at')
    list_filter = ('platform_name',)
    search_fields = ('sku__sku', 'platform_name')
    ordering = ('-dispatched_at',)
    list_per_page = 25

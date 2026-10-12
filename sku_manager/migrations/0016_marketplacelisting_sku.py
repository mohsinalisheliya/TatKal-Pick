from django.db import migrations, models
import django.db.models.deletion
from django.db.models.functions import Lower


class Migration(migrations.Migration):

    dependencies = [
        ("sku_manager", "0015_appsettings_label_logo"),
    ]

    operations = [
        migrations.CreateModel(
            name="MarketplaceListingSKU",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("listing_sku", models.CharField(max_length=100)),
                (
                    "platform",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="listing_skus",
                        to="sku_manager.platform",
                    ),
                ),
                (
                    "sku",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="marketplace_listing_skus",
                        to="sku_manager.jewelrysku",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("sku", "platform"),
                        name="uniq_market_sku_product",
                    ),
                    models.UniqueConstraint(
                        Lower("listing_sku"),
                        "platform",
                        name="uniq_market_sku_value_ci",
                    ),
                ],
            },
        ),
    ]

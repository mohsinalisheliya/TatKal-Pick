<div align="center">

<img src="./sku_manager/static/tatkal-pick-logo-horizontal.png" alt="Tatkal Pick" width="640">

<br><br>

**Inventory, warehouse location and QR-scan dispatch for jewelry & accessories sellers.**
Know exactly what you have, where it sits, and which batch it came from. Scan it, ship it, done.

<br>

![Django](https://img.shields.io/badge/Django-092E20?style=for-the-badge&logo=django&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white)
![QR](https://img.shields.io/badge/QR_Labels-0F172A?style=for-the-badge&logo=qrcode&logoColor=white)

</div>

---

## What is Tatkal Pick?

Tatkal Pick is a Django web app for selling jewelry and accessories on several channels at once
(Flipkart, Amazon, Meesho and your own website). One screen shows stock, batches, storage locations and
per-marketplace prices. Every product gets a printed QR sticker, and scanning it opens a
one-tap dispatch page that reduces stock and keeps an audit trail.

<div align="center">
  <img src="assets/icon-dark.svg#gh-light-mode-only" width="72" alt="">
  <img src="assets/icon-light.svg#gh-dark-mode-only" width="72" alt="">
</div>

## Features

| | |
|---|---|
| 🏷️ **Structured SKUs** | Auto-built from category, style, name, color, size and a serial number, e.g. `NCK-KRN-OVLROSE-GLD-FREE-009` |
| 📦 **Multi-batch stock** | Every inward lot is its own batch with quantity, cost, MRP, supplier and storage location |
| ⏸ **Hold a batch** | Disable sales for a batch without deleting it; its units stop counting as sellable stock |
| 📊 **Inventory dashboard** | Out of Stock / Low Stock / Sufficient cards, search, sort, adjustable low-stock limit, camera scan |
| 🧾 **Product detail page** | Image, all attributes, stock value, margin at MRP and per marketplace, sales by marketplace with date and time |
| 🛒 **Marketplace pricing** | A separate selling rate per platform, with profit or loss shown against your purchase cost |
| 🗄️ **Storage boxes** | Boxes and compartments with unique names and unique color tags (used colors are locked) |
| 📷 **QR scan and 1-tap dispatch** | Scan a sticker, pick the marketplace, and one unit leaves the oldest sellable batch (FIFO) |
| 🧮 **Dispatch log** | Every dispatch records platform, sold price, quantity and stock left |
| 🖨️ **Sticker printing** | Prints 50 × 25 mm labels, two per row on a 105 mm roll, with QR, product name, SKU and batch number |
| ♻️ **30-day batch retention** | Finished batches are kept for 30 days and then removed automatically |

## How it fits together

```
 Add product ──► Add stock (batch) ──► Print QR sticker ──► Stick on product
                        │                                           │
                        ▼                                           ▼
              Inventory & batches  ◄────── stock −1 ──────  Scan ► Dispatch
                                                                    │
                                                              Dispatch log
```

1. **Products** – create a product with its image; the SKU is generated for you.
2. **Stock** – add a batch (batch no, quantity, cost, MRP, marketplace rates, box and compartment).
3. **Label** – print stickers from the inventory page (🏷️ Label) or from a batch row.
4. **Dispatch** – scan the sticker with the in-app scanner; choose the marketplace; confirm.

## Getting started

> Adjust the commands to your own environment and `requirements.txt`.

```bash
# 1. clone and enter the project
git clone <your-repo-url> tatkal-pick
cd tatkal-pick

# 2. virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. dependencies (core ones used by the app)
pip install django pillow "qrcode[pil]"

# 4. database and admin user
python manage.py migrate
python manage.py createsuperuser

# 5. run
python manage.py runserver
```

Open `http://127.0.0.1:8000/` and sign in.

## QR codes and scanning

- The QR on a sticker contains **only the SKU ID** (for example `NCK-KRN-OVLROSE-GLD-FREE-009`).
- The scanner page and the inventory **Scan** button read a SKU and open `/dispatch/<sku>/` themselves.
- A phone's normal camera app will just show the text. Use the scanner page inside Tatkal Pick.
- Camera access in browsers needs `localhost` or HTTPS. When using it from a phone on your network, serve the app over HTTPS.

## Printing stickers

Built and tested for a **105 mm wide roll with two 50 × 25 mm stickers per row and no gap between the two** (such as a Helett H30C Lite label printer, 203 dpi, direct thermal).

| Setting | Value |
|---|---|
| Paper size in the print dialog | **105 × 25 mm** (one sticker row) |
| Media type in the printer driver | Label with gap, then run the printer's calibration once |
| Margins / scale | None / 100 % |
| Headers and footers | Off |
| Alignment | Use **Nudge X / Y** on the label page (saved in the browser) |
| Gap between the 2 stickers | Box on the label page, default `0` |

The print dialog should say **1 sheet** for 2 stickers. If it says more, the paper size does not match.
The QR is sized to a whole number of printer dots so it stays sharp and scans reliably on thermal paper.

## Project layout

```
sku_manager/
├── models.py          # JewelrySKU, StockBatch, StorageBox, Platform, PlatformPrice, DispatchLog, AppSettings
├── views.py           # products, inventory, stock, batches, boxes, dispatch, labels, scanner
└── templates/sku_manager/
    ├── inventory.html        # dashboard cards + product list
    ├── stock_action.html     # add / edit a batch
    ├── product_batches.html  # batch history, hold / enable, labels
    ├── product_detail.html   # full product page with pricing and sales
    ├── boxes.html            # storage units and color picker
    ├── scanner.html          # live camera scanner
    ├── dispatch.html         # 1-tap dispatch
    └── label_print.html      # 105 mm roll sticker layout
assets/                       # logos used by this README
```

## Brand assets

| Asset | File |
|---|---|
| **Horizontal logo** | `sku_manager/static/tatkal-pick-logo-horizontal.png` |
| **Horizontal logo (SVG)** | `sku_manager/static/tatkal-pick-logo-horizontal.svg` |
| **Icon** | `sku_manager/static/tatkal-pick-icon.svg` |

The logo is a single color: a parcel inside scan corners, for *pick* and *scan*. Colors are `#0F172A` (dark) and `#F8FAFC` (light).

## Roadmap ideas

- Low-stock alerts by email or WhatsApp
- Return and cancellation handling that puts the unit back into its batch
- Bulk label printing for all batches
- CSV import for products and stock

---

<div align="center">
<sub>Built with Django · Made for fast, accurate dispatch</sub>
</div>
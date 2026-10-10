# 🛍️ Local Product Catalog

A complete local-marketplace web application: **local shops publish their products**,
**customers browse, order, track and review them**, and **administrators run the whole
operation** — products, sellers, orders, coupons, reviews and analytics.

Built with **Python + Flask + Jinja2 + Bootstrap 5** on the front end and **MongoDB**
(NoSQL) as the backend database, accessed through **PyMongo** and inspected with
**MongoDB Compass**.

---

## 1. Objectives

To provide a dynamic web portal for managing a local product catalogue using a Python
backend and a NoSQL (MongoDB) database, with four distinct account types
(**user**, **customer**, **seller**, **admin**), role-based access control and full CRUD,
aggregation and analytics over the data.

---

## 2. Account types

| Role | Login | Landings & capabilities |
|------|-------|-------------------------|
| **User** | `/login` | Browse, filter, wishlist, cart, checkout, reviews, order history |
| **Customer** | `/login` | Everything a user can do **plus** a personal dashboard with spend/order statistics |
| **Seller** | `/login` | Shop owner: publishes and manages **only their own** products, sees their own sales and orders. Needs admin approval first |
| **Admin** | `/admin/login` | Full platform control: products, orders, users, sellers, coupons, reviews, analytics and CSV export |

Shoppers share one login portal because they have identical shopping permissions;
**administrators have a completely separate login page**, so the backend is never exposed
on the public form. Passwords are stored as salted hashes (`werkzeug.security`).

### Demo logins (created by `seed.py`)

| Role | Username | Password | Notes |
|------|----------|----------|-------|
| Admin | `admin` | `admin123` | Full access |
| Customer | `customer` | `customer123` | Has orders, reviews and wishlist items |
| User | `user` | `user123` | Basic shopper |
| Seller | `seller1` | `seller123` | **Approved** shop (Ramesh Fresh Fruits) |
| Seller | `seller2` | `seller123` | **Pending approval** — try approving them as admin |

### Demo coupons

| Code | Effect |
|------|--------|
| `WELCOME10` | 10 % off |
| `SAVE50` | ₹50 off orders over ₹300 |
| `FESTIVE20` | 20 % off orders over ₹500 (max 25 uses) |
| `EXPIRED5` | Disabled / expired — to demonstrate validation |

---

## 3. Features

**Storefront**
- Hero search, live catalogue statistics (products, shops, sellers, orders) and trending products
- **Advanced catalogue**: full-text search, category, shop, price range, minimum-rating and
  in-stock filters, four sort modes and **pagination**
- Product pages with rating breakdown bar chart, verified-purchase reviews, related
  products, view counter and **wishlist** toggle
- Session cart with stock validation, quantity editing, **coupon codes** and live totals
- Checkout with saved address, Cash on Delivery, discount application and stock reservation
- **Order tracking**: detail page with a status timeline, printable/PDF **invoice**,
  reorder, and customer self-cancellation (restores stock)
- **Wishlist** page, "recently viewed" row and dark-mode toggle

**Seller area**
- Application flow: register a shop → admin approves → product management unlocks
- Dashboard with sales revenue, units sold, order count and **low-stock alerts**
- Create/edit/delete **own** products only, with **image upload** (or URL)
- Orders page showing only that shop's line items, customer and shipping address

**Admin area**
- Analytics dashboard: gross revenue, average order value, discounts given, revenue-by-day
  line chart, orders-by-status doughnut, users-by-role doughnut, products-by-category bar
  chart, **top products**, **top shops**, **low-stock table** and latest orders
- Product CRUD with image upload and pagination
- Order management: status workflow (Placed → Processing → Shipped → Delivered / Cancelled),
  status filters, customer search and **CSV export**
- **Seller approval queue** (approve / reject applications)
- **User management**: role changes, per-user order counts, deletion with guard rails
  (cannot demote or delete yourself, cannot delete the last admin)
- **Coupon management**: create percent/flat coupons with minimum order, usage caps and expiry;
  enable/disable and delete
- **Review moderation** with rating filter
- Nav badges for new orders and pending seller applications

**Security & engineering**
- **CSRF protection** on every state-changing request (400 on missing/invalid token)
- **Login throttling**: 5 failed attempts locks the account+IP for 15 minutes
- Role-based access control decorators, ObjectId validation, open-redirect protection
- Server-side validation with input retention on every form
- Custom 400 / 403 / 404 / 500 error pages
- MongoDB indexes: unique username, unique review per user+product, unique wishlist entry,
  unique coupon code, text index for search, plus category/shop/status/date/seller indexes
- Friendly degradation if MongoDB is unreachable

---

## 4. Tech stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.10+ |
| Web framework | Flask 3 |
| Templates | Jinja2 (inheritance, blocks, filters, macros) |
| Styling | Bootstrap 5.3 (incl. `data-bs-theme` dark mode), Bootstrap Icons, custom CSS |
| Charts | Chart.js |
| Database | MongoDB 6/7 |
| DB driver | PyMongo 4 |
| DB GUI | MongoDB Compass |
| Config | python-dotenv (`.env`) |

---

## 5. Setup

```bash
pip install -r requirements.txt
```

Make sure MongoDB is running (Windows):

```powershell
Get-Service MongoDB          # check status
Start-Service MongoDB        # start if stopped
```

`python seed.py` then `python app.py`, and open <http://127.0.0.1:5000>.

`.env` (see `.env.example`):

```
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=local_product_catalog
SECRET_KEY=change-me-to-a-long-random-string
```

`python seed.py` is **idempotent** — it also backfills older documents so you can run it
again after upgrading.

---

## 6. MongoDB Compass

1. Open **MongoDB Compass**.
2. Connect to `mongodb://localhost:27017` (or `mongodb://user:pass@localhost:27017/?authSource=admin`).
3. Open the database **`local_product_catalog`**. Collections:

| Collection | Purpose | Key fields |
|------------|---------|------------|
| `users` | Accounts | `username` (unique), `password` (hash), `role`, `seller_status`, `shop_name`, `address`, `avatar_url` |
| `products` | Catalogue | `name`, `category`, `price`, `quantity`, `availability`, `seller_id`, `shop_name`, `views`, `image_url` |
| `orders` | Orders | `user_id`, `items[]` (with `shop_name`/`seller_id`), `subtotal`, `discount`, `coupon_code`, `total`, `status`, `status_history[]` |
| `reviews` | Reviews | `product_id`, `user_id`, `rating`, `comment`, `verified` |
| `wishlist` | Saved products | `user_id`, `product_id`, `added_at` |
| `coupons` | Discount codes | `code` (unique), `kind`, `value`, `min_order`, `max_uses`, `used_count`, `expires_at`, `active` |
| `login_attempts` | Brute-force throttle | `key` (`username|ip`), `failures`, `locked_until` |

Useful things to try in Compass:

- Filter `users` by `{ role: "seller" }` or `{ seller_status: "pending" }`.
- Filter `products` by `{ quantity: { $lte: 5 } }` to reproduce the low-stock report.
- **Aggregations** tab on `orders`: unwind `items`, group by `items.shop_name` and sum
  `items.price × items.quantity` to reproduce the "top shops" panel.
- **Schema** tab on any collection to inspect the document shape the app writes.

### MongoDB Atlas instead of local (optional)

Create a free cluster at <https://cloud.mongodb.com>, add your IP, create a user, then copy
the `mongodb+srv://…` string from *Connect → Compass* into `MONGO_URI` and re-run
`python seed.py`.

---

## 7. Project structure

```
.
├── app.py                     # Flask app: routes, auth, RBAC, MongoDB queries/aggregations
├── seed.py                    # Demo accounts, catalogue, coupons, orders, reviews, wishlist
├── verify.py                  # End-to-end HTTP checks (run while app.py is running)
├── requirements.txt
├── .env / .env.example
├── static/
│   ├── css/style.css          # Glassmorphism styling, timeline, dark mode
│   ├── js/script.js           # Dark-mode toggle, helpers
│   └── uploads/               # Product photos & avatars (git-ignored except .gitkeep)
└── templates/
    ├── base.html              # Layout: nav, badges, flash messages, dark mode, footer
    ├── index.html             # Hero, stats, trending, recently viewed, new arrivals
    ├── products.html          # Filters, sorting, pagination, ratings, wishlist
    ├── product_details.html   # Gallery, rating breakdown, reviews, related products
    ├── cart.html              # Quantity editing, coupon, order summary
    ├── checkout.html          # Address, payment, discounted totals
    ├── login.html / admin_login.html / register.html
    ├── account.html           # Profile, avatar upload, address, password
    ├── customer_dashboard.html
    ├── wishlist.html
    ├── my_orders.html         # Order history with cancel / reorder / invoice
    ├── order_detail.html      # Status timeline + actions
    ├── invoice.html           # Printable invoice
    ├── seller_status.html / seller_dashboard.html / seller_products.html
    ├── seller_product_form.html / seller_orders.html
    ├── admin_dashboard.html   # Analytics, charts, top products/shops, low stock
    ├── admin_products.html / add_product.html / edit_product.html
    ├── admin_orders.html / admin_users.html / admin_sellers.html
    ├── admin_coupons.html / admin_reviews.html
    └── errors/error.html      # 400 / 403 / 404 / 500
```

---

## 8. Route map

| Route | Methods | Access | Purpose |
|-------|---------|--------|---------|
| `/` | GET | public | Storefront home |
| `/products` | GET | public | Search / filter / sort / paginate catalogue |
| `/product/<id>` | GET | public | Product details, rating breakdown, reviews |
| `/product/<id>/review` | POST | shopper, seller | Create or update a review |
| `/product/<id>/review/delete` | POST | owner / admin | Delete a review |
| `/register` | GET, POST | public | Register user / customer / seller |
| `/login` | GET, POST | public | Shopper & seller login |
| `/admin/login` | GET, POST | public | Administrator login |
| `/logout` | GET | session | End session |
| `/account` | GET, POST | user/customer/seller | Profile, avatar, address, password |
| `/customer/dashboard` | GET | customer/seller/admin | Personal statistics |
| `/wishlist` | GET | shopper/seller | Saved products |
| `/wishlist/toggle/<id>` | POST | shopper/seller | Add / remove from wishlist |
| `/cart` | GET | public | Cart + coupon |
| `/cart/add/<id>` · `/cart/update/<id>` · `/cart/remove/<id>` | POST/GET | public | Cart operations |
| `/cart/coupon` · `/cart/coupon/remove` | POST | public | Apply / remove a coupon |
| `/checkout` | GET, POST | shopper/seller | Place an order |
| `/orders` | GET | shopper/seller | My order history |
| `/orders/<id>` | GET | owner / admin | Order tracking timeline |
| `/orders/<id>/invoice` | GET | owner / admin | Printable invoice |
| `/orders/<id>/cancel` | POST | owner | Cancel while Placed/Processing |
| `/orders/<id>/reorder` | POST | owner | Refill cart from an order |
| `/seller/status` | GET | seller | Approval status |
| `/seller` · `/seller/products` · `/seller/orders` | GET | approved seller | Shop dashboard, inventory, sales |
| `/seller/products/add` · `/seller/products/edit/<id>` | GET, POST | approved seller | Own-product CRUD |
| `/seller/products/delete/<id>` | POST | approved seller | Delete own product |
| `/admin` | GET | admin | Analytics dashboard |
| `/admin/products` | GET | admin | Paginated product management |
| `/admin/add` · `/admin/edit/<id>` | GET, POST | admin | Product CRUD |
| `/admin/delete/<id>` | POST | admin | Delete product |
| `/admin/orders` · `/admin/orders/<id>/status` | GET/POST | admin | Orders + status workflow |
| `/admin/orders/export` | GET | admin | CSV export |
| `/admin/users` · `/admin/users/<id>/role` · `/admin/users/<id>/delete` | GET/POST | admin | User management |
| `/admin/sellers` · `/admin/sellers/<id>/<approve\|reject>` | GET/POST | admin | Seller approval queue |
| `/admin/coupons` · `/admin/coupons/add` · `/admin/coupons/<id>/toggle` · `/admin/coupons/<id>/delete` | GET/POST | admin | Coupon management |
| `/admin/reviews` · `/admin/reviews/<id>/delete` | GET/POST | admin | Review moderation |

---

## 9. CO3 / CO4 mapping

**CO3 — Routing, Jinja2 inheritance, data passing, UI**
- Template inheritance: every page extends `base.html` (`{% extends %}`, `{% block %}`),
  with `{% block scripts %}` for per-page Chart.js code.
- Data passing: `render_template(..., products=…)` plus a context processor injecting the
  signed-in user, cart/wishlist counts, admin badge counts, status colours and money formatting.
- Static assets via `url_for('static', filename=…)`; uploaded images served from
  `static/uploads/`.
- Responsive Bootstrap 5 UI with dark mode, flash-message categories and an order timeline.

**CO4 — Complex MongoDB queries, CRUD and aggregation**
- **Create**: products, users, orders, reviews, wishlist entries, coupons.
- **Read**: `find_one`, `find().sort().skip().limit()` (pagination), `distinct()`,
  `count_documents()`.
- **Update**: `$set`, `$inc`, `$push`, `$setOnInsert`, conditional filters
  (`quantity $lte 0` auto-marks sold-out items), `upsert` for reviews.
- **Delete**: `delete_one` / `delete_many` with cascade cleanup (product → reviews, wishlist).
- **Aggregation pipelines**: products-per-category, users-per-role, orders-per-status,
  **revenue by day** (`$dateToString`), **top products** (`$unwind` + `$group` + `$multiply`),
  **top shops**, seller revenue scoping (`$match items.seller_id`), average rating per product.
- **Filtering**: `$regex` case-insensitive search across name/category/shop/description,
  `$or`, price range with `$gte`/`$lte`, min-rating via a review aggregation feeding `$in`.
- **Indexes**: unique `users.username`, unique `reviews(product_id,user_id)`, unique
  `wishlist(user_id,product_id)`, unique `coupons.code`, text index on product search fields,
  plus `category`, `shop_name`, `seller_id`, `views`, `status` and `date` indexes.
- **Integrity**: every URL ObjectId validated, per-role scoping, stock re-check before
  checkout, transactional-ish stock restore on cancellation.

---

## 10. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ServerSelectionTimeoutError` | MongoDB is not running — start the MongoDB service. |
| Admin login always fails | Run `python seed.py` to create the `admin` account. |
| "Too many failed attempts" | Login throttling; wait 15 minutes or clear the `login_attempts` collection. |
| 400 Bad request | The form's CSRF token expired — reload the page and submit again. |
| Charts empty | No products/orders yet; run `python seed.py`. |
| `IndexOptionsConflict` on seeding | Already handled by the seeder; re-run `python seed.py`. |
| Compass cannot connect | Confirm the service is running and the URI is `mongodb://localhost:27017`. |

---

## 11. Verification

Run the automated end-to-end check while the app is running:

```bash
python app.py          # terminal 1
python verify.py       # terminal 2  -> 89 checks, all should pass
python verify.py       # safe to re-run; it rolls the database back afterwards
```

`verify.py` drives the real HTTP endpoints and rolls back everything it creates — including
deleting the images it uploads — so the seeded demo data stays intact.

Covered: all four logins and their landing pages, role enforcement (403s), CSRF enforcement
(400s), login lockout, registration validation, catalogue filters/sorting/pagination/search,
wishlist add/remove, cart and coupon validation, checkout with discount, order tracking,
invoice, reorder, cancellation with stock restore, review upsert/verified flag/deletion,
seller publishing and cross-shop isolation, image upload (+ rejection of non-images),
pending-seller blocking and admin approval, admin analytics/CSV export/product CRUD/user
management, and logout redirects.

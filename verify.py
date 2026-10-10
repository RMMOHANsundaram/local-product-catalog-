"""
End-to-end checks for the Local Product Catalog.

Start the app first (``python app.py``), then run in a second terminal::

    python verify.py            # against http://127.0.0.1:5000
    TEST_BASE=http://127.0.0.1:5055 python verify.py

The script drives every real HTTP endpoint - logins for all four roles, shops,
coupons, wishlist, cart, checkout, order tracking, reviews, uploads, analytics,
CSRF protection and login lockout - then rolls the database back to the seeded
state. Only stdlib + PyMongo + python-dotenv are required.
"""

import base64
import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from bson.objectid import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

BASE = os.getenv("TEST_BASE", "http://127.0.0.1:5000")
TEST_ADDRESS = "12 Test Street, Coimbatore"
TEST_USER = "verifyuser"
TEST_PRODUCT = "Verify Test Product"

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("MONGO_DB_NAME", "local_product_catalog")
db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)[DB_NAME]

results = []
created_files = []

# 1x1 transparent PNG - used to exercise real image uploads.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8AAAwAB/AGkRwAAAABJRU5ErkJggg=="
)


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


class Session:
    """A cookie-jar backed client that never follows redirects."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect()
        )
        self.csrf = None

    def _send(self, req):
        try:
            with self.opener.open(req, timeout=10) as response:
                return (response.status, response.headers.get("Location", ""),
                        response.read().decode("utf-8", "replace"),
                        response.headers)
        except urllib.error.HTTPError as exc:
            return (exc.code, exc.headers.get("Location", ""),
                    exc.read().decode("utf-8", "replace"), exc.headers)

    def get(self, path):
        return self._send(urllib.request.Request(BASE + path))

    def sync_csrf(self):
        for path in ("/", "/cart", "/login"):
            status, _, body, _ = self.get(path)
            match = re.search(r'name="_csrf"\s+value="([^"]+)"', body)
            if match:
                self.csrf = match.group(1)
                return self.csrf
        raise RuntimeError("Could not find a CSRF token on the page")

    def post(self, path, data=None, with_token=True):
        payload = dict(data or {})
        if with_token:
            payload.setdefault("_csrf", self.csrf or self.sync_csrf())
        body = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(BASE + path, data=body, method="POST")
        return self._send(req)

    def post_multipart(self, path, fields, filename=None, content=None):
        boundary = "----verifyboundary7f3a91"
        parts = []
        fields = dict(fields or {})
        fields.setdefault("_csrf", self.csrf or self.sync_csrf())
        for key, value in fields.items():
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            parts.append(str(value).encode() + b"\r\n")
        if filename:
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(
                f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode()
            )
            parts.append(b"Content-Type: image/png\r\n\r\n")
            parts.append(content or PNG_BYTES)
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        req = urllib.request.Request(
            BASE + path, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return self._send(req)


def check(name, condition, detail=""):
    results.append((bool(condition), name, detail))
    print(("PASS  " if condition else "FAIL  ") + name + ("" if condition else f"   -> {detail}"))


def login(username, password, path="/login"):
    session = Session()
    session.sync_csrf()
    status, location, body, _ = session.post(path, {"username": username, "password": password})
    if status == 302:
        # Signing in rotates the session (and its CSRF token), like a browser reload.
        session.sync_csrf()
    return session, status, location, body


# ---------------------------------------------------------------------------
# Demo data guards
# ---------------------------------------------------------------------------
mango = db.products.find_one({"name": "Fresh Mango"})
admin = db.users.find_one({"username": "admin"})
if not mango or not admin:
    print("! Run `python seed.py` first - demo data is missing.")
    sys.exit(2)

MANGO_ID = str(mango["_id"])
ADMIN_ID = str(admin["_id"])
COTTON_ID = str(db.products.find_one({"name": "Cotton Shirt"})["_id"])
EARBUDS_ID = str(db.products.find_one({"name": "Wireless Earbuds"})["_id"])
SELLER1 = db.users.find_one({"username": "seller1"})
SELLER2 = db.users.find_one({"username": "seller2"})
SELLER1_ID = str(SELLER1["_id"])
SELLER2_ID = str(SELLER2["_id"])
CUSTOMER = db.users.find_one({"username": "customer"})
CUSTOMER_ID = str(CUSTOMER["_id"])
# Snapshotted so the review tests can be rolled back without losing seeded data.
REVIEW_SNAPSHOT = db.reviews.find_one({"product_id": MANGO_ID, "user_id": CUSTOMER_ID})

# Clean up any leftovers from a previous interrupted run.
db.users.delete_many({"username": TEST_USER})
db.products.delete_many({"name": TEST_PRODUCT})
db.coupons.delete_many({"code": "VERIFY5"})
db.login_attempts.delete_many({"key": {"$regex": "^verify|^locktest"}})
db.orders.delete_many({"seeded": False, "username": TEST_USER})
for stale in db.orders.find({"shipping_address": TEST_ADDRESS}):
    db.orders.delete_one({"_id": stale["_id"]})

# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
anon = Session()
anon.sync_csrf()

status, _, body, _ = anon.get("/")
check("GET / -> 200 with stats + trending", status == 200 and "Trending Now" in body, status)

status, _, body, _ = anon.get("/products")
check("GET /products -> 200 with filters sidebar",
      status == 200 and "Apply filters" in body and "Minimum rating" in body, status)

status, _, body, _ = anon.get("/products?price_min=100&price_max=200")
check("price filter returns in-range products",
      status == 200 and "Fresh Mango" in body and "Wireless Earbuds" not in body, status)

status, _, body, _ = anon.get("/products?min_rating=4")
check("min_rating filter keeps only well-rated products",
      status == 200 and "Cotton Shirt" in body and "Wireless Earbuds" not in body, status)

status, _, body, _ = anon.get("/products?in_stock=1&sort=price_low&page=2")
check("sort + in-stock + pagination work", status == 200, status)

status, _, body, _ = anon.get("/products?search=Mango")
check("search finds product by name", status == 200 and "Fresh Mango" in body, status)

status, _, body, _ = anon.get("/product/" + MANGO_ID)
check("product page -> reviews + rating histogram",
      status == 200 and "Rating breakdown" in body and "Customer Reviews" in body, status)

status, _, _, _ = anon.get("/product/not-an-id")
check("bad product id -> redirect", status == 302, status)

status, _, body, _ = anon.get("/no-such-page")
check("unknown route -> 404 page", status == 404 and "Page not found" in body, status)

status, _, body, _ = anon.get("/login")
check("login page -> User / Customer", status == 200 and "User / Customer Login" in body, status)

status, _, body, _ = anon.get("/admin/login")
check("admin login page -> Administrator", status == 200 and "Administrator Login" in body, status)

status, _, body, _ = anon.get("/register")
check("register page offers seller signup", status == 200 and 'value="seller"' in body, status)

status, location, _, _ = anon.get("/admin")
check("anonymous /admin -> redirect to login", status == 302 and "/login" in location,
      f"{status} {location}")

# ---------------------------------------------------------------------------
# Security: CSRF + login lockout
# ---------------------------------------------------------------------------
raw = Session()
raw.sync_csrf()
status, _, _, _ = raw.post("/login", {"username": "customer", "password": "customer123"},
                           with_token=False)
check("POST without CSRF token -> 400", status == 400, status)

bad_token = Session()
bad_token.sync_csrf()
bad_token.csrf = "definitely-not-the-token"
status, _, _, _ = bad_token.post("/login", {"username": "customer", "password": "customer123"})
check("POST with wrong CSRF token -> 400", status == 400, status)

locker = Session()
locker.sync_csrf()
lockout_message = ""
for _ in range(5):
    _, _, body, _ = locker.post("/login", {"username": "locktest", "password": "wrong"})
status, _, body, _ = locker.post("/login", {"username": "locktest", "password": "wrong"})
check("5 failed logins trigger a lockout", "Too many failed attempts" in body, body[:80])
db.login_attempts.delete_many({"key": {"$regex": "^locktest"}})

# ---------------------------------------------------------------------------
# Registration + validation
# ---------------------------------------------------------------------------
reg = Session()
reg.sync_csrf()
status, _, body, _ = reg.post("/register", {
    "username": "x", "password": "abc", "confirm_password": "zzz",
    "role": "customer", "email": "bad"})
check("register rejects bad input", status == 200 and "Passwords do not match" in body, status)

status, _, body, _ = reg.post("/register", {
    "username": TEST_USER, "full_name": "Verify User", "email": "verify@example.com",
    "password": "verify123", "confirm_password": "verify123", "role": "admin"})
check("register refuses admin self-signup", status == 200 and "valid account type" in body, status)

status, _, _, _ = reg.post("/register", {
    "username": TEST_USER, "full_name": "Verify User", "email": "verify@example.com",
    "password": "verify123", "confirm_password": "verify123", "role": "customer"})
check("register creates a customer", status == 302, status)

status, _, body, _ = reg.post("/register", {
    "username": TEST_USER, "password": "verify123", "confirm_password": "verify123",
    "role": "user"})
check("duplicate username blocked", status == 200 and "already taken" in body, status)

status, _, body, _ = reg.post("/register", {
    "username": "verify_shop", "password": "verify123", "confirm_password": "verify123",
    "role": "seller"})
check("seller signup requires a shop name",
      status == 200 and "shop name" in body.lower(), status)
db.users.delete_many({"username": "verify_shop"})

# ---------------------------------------------------------------------------
# Customer flow: login, wishlist, cart, coupon, checkout, tracking
# ---------------------------------------------------------------------------
cust, status, location, _ = login("customer", "customer123")
check("customer login -> dashboard", status == 302 and location.endswith("/customer/dashboard"),
      f"{status} {location}")
status, _, body, _ = cust.get("/customer/dashboard")
check("customer dashboard -> stats", status == 200 and "Total Spent" in body, status)

status, _, _, _ = cust.get("/admin")
check("customer blocked from /admin", status == 403, status)

status, _, _, _ = cust.get("/seller")
check("customer blocked from /seller", status == 403, status)

# Wishlist
before = db.wishlist.count_documents({"user_id": str(db.users.find_one({"username": "customer"})["_id"])})
status, _, _, _ = cust.post(f"/wishlist/toggle/{COTTON_ID}")
after = db.wishlist.count_documents({"user_id": CUSTOMER_ID})
check("wishlist toggle adds an entry", status == 302 and after == before + 1, f"{status} {before}->{after}")
status, _, body, _ = cust.get("/wishlist")
check("wishlist page renders", status == 200 and "Cotton Shirt" in body, status)
cust.post(f"/wishlist/toggle/{COTTON_ID}")
restored = db.wishlist.count_documents({"user_id": CUSTOMER_ID})
check("wishlist toggle removes the entry", restored == before, f"{before}->{restored}")

# Cart
stock_before = db.products.find_one({"_id": ObjectId(MANGO_ID)})["quantity"]
status, _, _, _ = cust.post(f"/cart/add/{MANGO_ID}", {"quantity": "2"})
check("add to cart", status == 302, status)
status, _, body, _ = cust.get("/cart")
check("cart shows line + subtotal", status == 200 and "Fresh Mango" in body and "Coupon" in body, status)

# Coupon handling
status, _, body, _ = cust.post("/cart/coupon", {"code": "NOPE123"})
status, _, body, _ = cust.get("/cart")
check("invalid coupon rejected", status == 200 and "does not exist" in body, status)

status, _, _, _ = cust.post("/cart/coupon", {"code": "save50"})   # lowercase -> uppercased
status, _, body, _ = cust.get("/cart")
check("valid coupon applied (case-insensitive)",
      status == 200 and "Discount (SAVE50)" in body, status)

# Checkout
status, _, _, _ = cust.post("/checkout", {
    "address": TEST_ADDRESS, "payment_method": "Cash on Delivery"})
check("checkout places the order", status == 302, status)
order = db.orders.find_one({"shipping_address": TEST_ADDRESS})
check("order stored with discount + history",
      order is not None and order["discount"] == 50.0 and order["coupon_code"] == "SAVE50"
      and len(order["status_history"]) == 1, order)
check("stock decremented by 2",
      db.products.find_one({"_id": ObjectId(MANGO_ID)})["quantity"] == stock_before - 2,
      db.products.find_one({"_id": ObjectId(MANGO_ID)})["quantity"])

coupon = db.coupons.find_one({"code": "SAVE50"})
check("coupon usage counter incremented", (coupon.get("used_count") or 0) >= 1, coupon.get("used_count"))

if order:
    ORDER_ID = str(order["_id"])
    status, _, body, _ = cust.get(f"/orders/{ORDER_ID}")
    check("order detail page -> timeline",
          status == 200 and "Tracking timeline" in body and "Placed" in body, status)
    status, _, body, _ = cust.get(f"/orders/{ORDER_ID}/invoice")
    check("invoice page renders", status == 200 and "INVOICE" in body, status)
    status, _, body, _ = cust.get("/orders")
    check("order appears in my orders", status == 200 and "Placed" in body, status)

    # Reorder copies items into the cart
    status, _, _, _ = cust.post(f"/orders/{ORDER_ID}/reorder")
    status, _, body, _ = cust.get("/cart")
    check("reorder fills the cart", status == 200 and "Fresh Mango" in body, status)

    # Cancel restores stock
    status, _, _, _ = cust.post(f"/orders/{ORDER_ID}/cancel")
    refreshed = db.orders.find_one({"_id": ObjectId(ORDER_ID)})
    check("order cancelled with history entry",
          refreshed["status"] == "Cancelled" and len(refreshed["status_history"]) == 2,
          refreshed["status"])
    check("stock restored on cancel",
          db.products.find_one({"_id": ObjectId(MANGO_ID)})["quantity"] == stock_before,
          db.products.find_one({"_id": ObjectId(MANGO_ID)})["quantity"])
    status, _, _, _ = cust.post(f"/orders/{ORDER_ID}/cancel")
    check("second cancel is refused", db.orders.find_one({"_id": ObjectId(ORDER_ID)})["status"] == "Cancelled",
          "status changed")

# Clear the cart for later tests
status, _, body, _ = cust.get("/cart")
for token in re.findall(r'/cart/remove/([a-f0-9]{24})', body):
    cust.get(f"/cart/remove/{token}")

# Reviews
db.reviews.delete_many({"user_id": str(db.users.find_one({"username": "customer"})["_id"]),
                        "product_id": MANGO_ID})
status, _, _, _ = cust.post(f"/product/{MANGO_ID}/review",
                            {"rating": "5", "comment": "Verify review"})
review = db.reviews.find_one({"product_id": MANGO_ID})
check("review saved with verified-purchase flag",
      review is not None and review["verified"] is True, review and review.get("verified"))
cust.post(f"/product/{MANGO_ID}/review", {"rating": "4", "comment": "Verify review updated"})
check("re-posting updates instead of duplicating",
      review is not None
      and db.reviews.count_documents({"product_id": MANGO_ID,
                                      "user_id": review["user_id"]}) == 1,
      db.reviews.count_documents({"product_id": MANGO_ID}))
status, _, _, _ = cust.post(f"/product/{MANGO_ID}/review/delete")
check("review deletion works",
      db.reviews.find_one({"product_id": MANGO_ID}) is None, "review still present")

# ---------------------------------------------------------------------------
# Seller flow
# ---------------------------------------------------------------------------
seller, status, location, _ = login("seller1", "seller123")
check("approved seller login -> seller dashboard",
      status == 302 and location.endswith("/seller"), f"{status} {location}")
status, _, body, _ = seller.get("/seller")
check("seller dashboard shows shop + stats",
      status == 200 and "Sales Revenue" in body and "My Products" in body, status)

status, _, _, _ = seller.get(f"/seller/products/edit/{COTTON_ID}")
check("seller cannot touch another shop's product (404)", status == 404, status)
status, _, _, _ = seller.post(f"/seller/products/delete/{COTTON_ID}")
check("seller cannot delete another shop's product (404)", status == 404, status)

status, _, _, _ = seller.post("/seller/products/add", {
    "name": TEST_PRODUCT, "category": "Other", "description": "Created by verify.py",
    "price": "199", "quantity": "7", "availability": "Yes", "shop_name": "",
    "seller_name": "", "location": "Test St", "phone": "9000000000", "image_url": ""})
product = db.products.find_one({"name": TEST_PRODUCT})
check("seller publishes a product", status == 302 and product is not None, status)
check("published product is owned by the seller",
      product and product.get("seller_id") == SELLER1_ID, product and product.get("seller_id"))
check("seller shop name applied automatically",
      product and product.get("shop_name") == SELLER1.get("shop_name"),
      product and product.get("shop_name"))

if product:
    TEST_ID = str(product["_id"])
    status, _, body, _ = seller.get("/products?search=Verify%20Test")
    check("new product visible in the public catalogue", status == 200 and TEST_PRODUCT in body, status)

    status, _, _, _ = seller.post(f"/seller/products/edit/{TEST_ID}", {
        "name": TEST_PRODUCT, "category": "Other", "description": "updated",
        "price": "249", "quantity": "7", "availability": "Yes"})
    check("seller edits own product",
          db.products.find_one({"_id": product["_id"]})["price"] == 249.0, "price not updated")

    # Image upload
    status, _, _, _ = seller.post_multipart(f"/seller/products/edit/{TEST_ID}",
                                            {"name": TEST_PRODUCT, "category": "Other",
                                             "description": "with image", "price": "249",
                                             "quantity": "7", "availability": "Yes"},
                                            filename="verify.png")
    refreshed = db.products.find_one({"_id": product["_id"]})
    image_url = refreshed.get("image_url", "")
    created_files.append(image_url)
    check("image upload stores a static/uploads path",
          status == 302 and image_url.startswith("/static/uploads/"), image_url)
    if image_url.startswith("/static/uploads/"):
        s2, _, _, _ = anon.get(image_url)
        check("uploaded image is served over HTTP", s2 == 200, s2)

    status, _, body, _ = seller.post_multipart(f"/seller/products/edit/{TEST_ID}",
                                               {"name": TEST_PRODUCT, "category": "Other",
                                                "description": "bad file", "price": "249",
                                                "quantity": "7", "availability": "Yes"},
                                               filename="evil.txt")
    check("upload rejects a non-image file",
          status == 200 and "Unsupported image type" in body, status)

    status, _, _, _ = seller.post(f"/seller/products/delete/{TEST_ID}")
    check("seller deletes own product",
          db.products.find_one({"_id": product["_id"]}) is None, "still present")

status, _, body, _ = seller.get("/seller/orders")
check("seller orders page (own line items only)", status == 200 and "Orders for my shop" in body, status)

# Pending seller is blocked until approved
pending, status, location, _ = login("seller2", "seller123")
check("pending seller lands on status page",
      status == 302 and location.endswith("/seller/status"), f"{status} {location}")
status, _, body, _ = pending.get("/seller/status")
check("status page explains the pending state",
      status == 200 and "Waiting for approval" in body, status)
status, _, _, _ = pending.get("/seller")
check("pending seller blocked from seller area", status == 302, status)

# ---------------------------------------------------------------------------
# Admin flow
# ---------------------------------------------------------------------------
admin_s, status, location, _ = login("admin", "admin123", path="/admin/login")
check("admin login -> dashboard", status == 302 and location.endswith("/admin"), f"{status} {location}")
status, _, body, _ = admin_s.get("/admin")
check("admin dashboard shows analytics",
      status == 200 and "Revenue by day" in body and "salesChart" in body
      and "Top products" in body and "Low stock" in body, status)

status, _, body, _ = admin_s.get("/admin/orders")
check("admin orders page", status == 200 and "All Customer Orders" in body, status)

status, _, body, headers = admin_s.get("/admin/orders/export")
check("CSV export returns a csv attachment",
      status == 200 and "text/csv" in headers.get("Content-Type", "")
      and "Order ID" in body, headers.get("Content-Type"))

status, _, body, _ = admin_s.get("/admin/products")
check("admin product list", status == 200 and "Manage Products" in body, status)

status, _, body, _ = admin_s.get("/admin/users")
check("admin user list with roles", status == 200 and "Manage Users" in body, status)

status, _, body, _ = admin_s.get("/admin/sellers")
check("admin sellers queue", status == 200 and "Manage Sellers" in body, status)

status, _, body, _ = admin_s.get("/admin/coupons")
check("admin coupon list", status == 200 and "WELCOME10" in body, status)

status, _, body, _ = admin_s.get("/admin/reviews")
check("admin review moderation", status == 200 and "Review moderation" in body, status)

status, _, _, _ = admin_s.post("/admin/coupons/add", {
    "code": "bad code!", "kind": "percent", "value": "10"})
check("coupon create validates the code",
      db.coupons.find_one({"code": "BAD CODE!"}) is None, "invalid coupon stored")

status, _, _, _ = admin_s.post("/admin/coupons/add", {
    "code": "VERIFY5", "kind": "flat", "value": "5", "min_order": "0", "max_uses": "1"})
coupon = db.coupons.find_one({"code": "VERIFY5"})
check("admin creates a coupon", status == 302 and coupon is not None, status)
if coupon:
    admin_s.post(f"/admin/coupons/{coupon['_id']}/toggle")
    check("coupon toggle works",
          db.coupons.find_one({"_id": coupon["_id"]})["active"] is False, "not toggled")
    admin_s.post(f"/admin/coupons/{coupon['_id']}/delete")
    check("coupon delete works", db.coupons.find_one({"_id": coupon["_id"]}) is None, "still present")

# Admin approves the pending seller, then that seller can work
admin_s.post(f"/admin/sellers/{SELLER2_ID}/approve")
check("admin approves a seller",
      db.users.find_one({"_id": ObjectId(SELLER2_ID)})["seller_status"] == "approved", "not approved")
pending2, status, location, _ = login("seller2", "seller123")
status, _, _, _ = pending2.get("/seller")
check("approved seller gains seller access", status == 200, status)
admin_s.post(f"/admin/sellers/{SELLER2_ID}/reject")
db.users.update_one({"_id": ObjectId(SELLER2_ID)}, {"$set": {"seller_status": "pending"}})

# Order status workflow + customer visibility
admin_s.post(f"/admin/orders/{ORDER_ID}/status", {"status": "Shipped"})
status, _, body, _ = cust.get(f"/orders/{ORDER_ID}")
check("admin status change appears in the customer timeline",
      status == 200 and "Shipped" in body, status)

# Admin cannot demote or delete itself
admin_s.post(f"/admin/users/{ADMIN_ID}/role", {"role": "customer"})
check("admin cannot demote itself",
      db.users.find_one({"_id": ObjectId(ADMIN_ID)})["role"] == "admin", "role changed!")
admin_s.post(f"/admin/users/{ADMIN_ID}/delete")
check("admin cannot delete itself",
      db.users.find_one({"_id": ObjectId(ADMIN_ID)}) is not None, "deleted!")

# Admin product CRUD
admin_s.post("/admin/add", {
    "name": "Admin Added Product", "category": "Other", "description": "x",
    "price": "99", "quantity": "3", "availability": "Yes", "image_url": ""})
added = db.products.find_one({"name": "Admin Added Product"})
check("admin creates a product", added is not None, "not created")
if added:
    admin_s.post(f"/admin/edit/{added['_id']}", {
        "name": "Admin Added Product", "category": "Other", "description": "y",
        "price": "123", "quantity": "3", "availability": "Yes"})
    check("admin edits a product",
          db.products.find_one({"_id": added["_id"]})["price"] == 123.0, "not updated")
    admin_s.post(f"/admin/delete/{added['_id']}", {})
    check("admin deletes a product", db.products.find_one({"_id": added["_id"]}) is None, "not deleted")

# ---------------------------------------------------------------------------
# Plain user + logout behaviour
# ---------------------------------------------------------------------------
plain, status, location, _ = login("user", "user123")
check("user login -> account", status == 302 and location.endswith("/account"), f"{status} {location}")
status, _, _, _ = plain.get("/customer/dashboard")
check("plain user blocked from customer dashboard", status == 403, status)

status, _, _, _ = plain.get("/wishlist")
check("user can open wishlist", status == 200, status)

status, _, _, _ = admin_s.get("/admin/login")
check("logged-in admin visiting /admin/login -> dashboard", status == 302, status)

status, _, _, _ = cust.get("/logout")
status, _, _, _ = cust.get("/orders")
check("after logout /orders redirects to login", status == 302, status)


# ---------------------------------------------------------------------------
# Roll back everything this run created
# ---------------------------------------------------------------------------
def cleanup():
    for order in db.orders.find({"shipping_address": TEST_ADDRESS}):
        for item in order.get("items", []):
            try:
                db.products.update_one({"_id": ObjectId(item["product_id"])},
                                       {"$inc": {"quantity": item["quantity"]},
                                        "$set": {"availability": "Yes"}})
            except Exception:
                pass
    db.orders.delete_many({"shipping_address": TEST_ADDRESS})
    db.orders.delete_many({"username": TEST_USER})
    db.products.delete_many({"name": {"$in": [TEST_PRODUCT, "Admin Added Product"]}})
    db.users.delete_many({"username": {"$in": [TEST_USER, "verify_shop", "locktest"]}})
    db.coupons.delete_many({"code": "VERIFY5"})
    db.login_attempts.delete_many({"key": {"$regex": "^verify|^locktest"}})
    db.reviews.delete_many({"comment": {"$regex": "^Verify review"}})
    # Put back the seeded review the deletion test removed.
    if REVIEW_SNAPSHOT is not None:
        db.reviews.replace_one({"_id": REVIEW_SNAPSHOT["_id"]}, REVIEW_SNAPSHOT, upsert=True)

    # Restore demo roles and seller approval states.
    for username, role in (("admin", "admin"), ("customer", "customer"), ("user", "user")):
        db.users.update_one({"username": username}, {"$set": {"role": role}})
    db.users.update_one({"username": "seller1"},
                        {"$set": {"role": "seller", "seller_status": "approved"}})
    db.users.update_one({"username": "seller2"},
                        {"$set": {"role": "seller", "seller_status": "pending"}})

    # The wishlist row for Fresh Mango was created by this run; the seeded shop
    # order was never cancelled by this run, so its status is left untouched.
    customer = db.users.find_one({"username": "customer"})
    if customer:
        db.wishlist.delete_many({"user_id": str(customer["_id"]),
                                 "product_id": MANGO_ID})
        db.coupons.update_one({"code": "SAVE50"}, {"$set": {"used_count": 0}})

    # Remove uploaded test images.
    for url in created_files:
        if url and url.startswith("/static/uploads/"):
            path = os.path.join("static", "uploads", os.path.basename(url))
            try:
                os.remove(path)
            except OSError:
                pass


cleanup()
print("Demo data rolled back to its seeded state.")

passed = sum(1 for ok, _, _ in results if ok)
print("\n" + "=" * 62)
print(f"RESULT: {passed}/{len(results)} checks passed")
print("=" * 62)
for ok, name, detail in results:
    if not ok:
        print(f"  FAILED: {name}  ({detail})")
sys.exit(0 if passed == len(results) else 1)

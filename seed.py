"""
Database seeder for the Local Product Catalog.

Creates demo accounts for every role (admin / customer / user / seller), a
catalogue with shop-owned products, discount coupons, sample orders with
tracking history, verified reviews and wishlist entries.

Idempotent: run it as often as you like - existing documents are matched by
username / product name / coupon code and left untouched.

Usage::

    python seed.py

Then connect MongoDB Compass to ``mongodb://localhost:27017`` and open the
database ``local_product_catalog``.
"""

import datetime
import os
import re
import sys

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError
from werkzeug.security import generate_password_hash

# Windows consoles often default to cp1252 which cannot encode ₹ / ✓.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("MONGO_DB_NAME", "local_product_catalog")

IMG = "https://images.unsplash.com/photo-{}?w=800"
# Branded placeholder used when a third-party product photo is unavailable.
PLACEHOLDER = "https://placehold.co/800x600/4f46e5/ffffff/png?text={}"

# Third-party photo ids that have gone dead since the project was first written.
# The seeder repairs these exact URLs so the demo catalogue never shows a
# broken image (mongo docs holding one of these keys are updated in place).
LEGACY_IMAGE_FIXES = {
    "https://images.unsplash.com/photo-1581691238478-f71694a11f93?w=500":
        PLACEHOLDER.format("Handmade+Basket"),
    "https://images.unsplash.com/photo-1587049352847-81a56d773c1c?w=500":
        PLACEHOLDER.format("Organic+Honey"),
    "https://images.unsplash.com/photo-1596755094514-f87e32f85e2c?w=500":
        "https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=800",
    "https://images.unsplash.com/photo-1626082895617-2c6c747051df?w=500":
        PLACEHOLDER.format("Local+Snacks"),
    "https://images.unsplash.com/photo-1531346878377-a541e4ab0e17?w=500":
        PLACEHOLDER.format("Notebook"),
}

# ---------------------------------------------------------------------------
# Demo accounts
# ---------------------------------------------------------------------------
DEMO_USERS = [
    {
        "username": "admin", "password": "admin123", "role": "admin",
        "full_name": "Store Administrator", "email": "admin@localcatalog.test",
        "phone": "9876500000", "address": "Admin Office, Coimbatore 641001",
    },
    {
        "username": "customer", "password": "customer123", "role": "customer",
        "full_name": "Priya Customer", "email": "customer@localcatalog.test",
        "phone": "9876500001", "address": "12, Gandhi Street, Coimbatore - 641001",
    },
    {
        "username": "user", "password": "user123", "role": "user",
        "full_name": "Ravi User", "email": "user@localcatalog.test",
        "phone": "9876500002", "address": "44, Nehru Nagar, Salem - 636001",
    },
    {
        "username": "seller1", "password": "seller123", "role": "seller",
        "full_name": "Ramesh Kumar", "email": "ramesh@localcatalog.test",
        "phone": "9876500003", "address": "Market Road, Coimbatore",
        "seller_status": "approved", "shop_name": "Ramesh Fresh Fruits",
        "shop_about": "Farm-fresh fruits and vegetables picked every morning.",
    },
    {
        "username": "seller2", "password": "seller123", "role": "seller",
        "full_name": "Anita Devi", "email": "anita@localcatalog.test",
        "phone": "9876500004", "address": "Crafts Village, Coimbatore",
        "seller_status": "pending", "shop_name": "Anita Handicrafts",
        "shop_about": "Hand-woven baskets, mats and traditional crafts.",
    },
]

# ---------------------------------------------------------------------------
# Catalogue - owner is "seller1" / "seller2" or None (platform/managed stock)
# ---------------------------------------------------------------------------
SAMPLE_PRODUCTS = [
    {"name": "Fresh Mango", "category": "Food", "price": 150, "quantity": 50,
     "owner": "seller1", "location": "Market Road", "phone": "9876543210",
     "description": "Sweet organic Alphonso mangoes sourced from local farms.",
     "image_url": IMG.format("1553279768-865429fa0078"), "views": 143},
    {"name": "Organic Rice 5kg", "category": "Grocery", "price": 450, "quantity": 35,
     "owner": "seller1", "location": "Market Road", "phone": "9876543210",
     "description": "Premium organic sona masoori rice, 5 kg pack.",
     "image_url": IMG.format("1586201375761-83865001e31c"), "views": 98},
    {"name": "Handmade Basket", "category": "Handmade", "price": 300, "quantity": 4,
     "owner": "seller2", "location": "Crafts Village", "phone": "9876543212",
     "description": "Hand-woven cane basket, perfect for home decor and storage.",
     "image_url": PLACEHOLDER.format("Handmade+Basket"), "views": 76},
    {"name": "Cotton Shirt", "category": "Clothing", "price": 500, "quantity": 20,
     "owner": None, "location": "Main Street", "phone": "9876543211",
     "description": "100% cotton casual shirt available in all sizes.",
     "image_url": "https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=800",
     "views": 61},
    {"name": "Wireless Earbuds", "category": "Electronics", "price": 1299, "quantity": 15,
     "owner": None, "location": "Tech Market", "phone": "9876543214",
     "description": "Bluetooth 5.3 earbuds with 24-hour battery backup.",
     "image_url": IMG.format("1590658268037-6bf12165a8df"), "views": 210},
    {"name": "Ceramic Tea Set", "category": "Household", "price": 899, "quantity": 3,
     "owner": None, "location": "Station Road", "phone": "9876543215",
     "description": "12-piece hand-painted ceramic tea set for six people.",
     "image_url": IMG.format("1563822249366-3efb23b8e0c9"), "views": 44},
    {"name": "Notebook Pack of 5", "category": "Stationery", "price": 220, "quantity": 60,
     "owner": None, "location": "College Road", "phone": "9876543216",
     "description": "200-page ruled notebooks, perfect for students.",
     "image_url": IMG.format("1531346878377-a5be20888e57"), "views": 33},
    {"name": "Herbal Face Wash", "category": "Beauty", "price": 180, "quantity": 40,
     "owner": None, "location": "Main Street", "phone": "9876543217",
     "description": "Neem and tulsi face wash, gentle on all skin types.",
     "image_url": IMG.format("1556228720-195a672e8a03"), "views": 88},
]

# ---------------------------------------------------------------------------
# Coupons
# ---------------------------------------------------------------------------
SAMPLE_COUPONS = [
    {"code": "WELCOME10", "kind": "percent", "value": 10, "min_order": 0,
     "max_uses": 0, "expires_at": None, "active": True},
    {"code": "SAVE50", "kind": "flat", "value": 50, "min_order": 300,
     "max_uses": 0, "expires_at": None, "active": True},
    {"code": "FESTIVE20", "kind": "percent", "value": 20, "min_order": 500,
     "max_uses": 25, "expires_at": None, "active": True},
    {"code": "EXPIRED5", "kind": "flat", "value": 5, "min_order": 0,
     "max_uses": 0, "expires_at": datetime.datetime(2020, 1, 1), "active": False},
]


def days_ago(days):
    return datetime.datetime.utcnow() - datetime.timedelta(days=days)


# ---------------------------------------------------------------------------
def ensure_index(collection, keys, **kwargs):
    """Create an index, replacing a stale conflicting one when needed.

    MongoDB allows only one text index per collection, so an older index with
    different fields has to be dropped before the new one can be created.
    """
    try:
        collection.create_index(keys, **kwargs)
    except OperationFailure as exc:
        if exc.code != 85:  # 85 = IndexOptionsConflict
            raise
        for info in collection.list_indexes():
            if "_fts" in (info.get("key") or {}):
                collection.drop_index(info["name"])
        collection.create_index(keys, **kwargs)


def create_indexes(db):
    """Indexes keep search / filter / lookup queries fast."""
    db.users.create_index("username", unique=True)
    ensure_index(db.products, [("name", "text"), ("category", "text"),
                               ("shop_name", "text"), ("description", "text")])
    db.products.create_index("category")
    db.products.create_index("shop_name")
    db.products.create_index("created_at")
    db.products.create_index("seller_id")
    db.products.create_index("views")
    db.orders.create_index("user_id")
    db.orders.create_index("date")
    db.orders.create_index("status")
    db.orders.create_index("items.seller_id")
    db.reviews.create_index([("product_id", 1), ("user_id", 1)], unique=True)
    db.wishlist.create_index([("user_id", 1), ("product_id", 1)], unique=True)
    db.coupons.create_index("code", unique=True)
    print("[OK] Indexes ensured")


def seed_users(db):
    created = 0
    for spec in DEMO_USERS:
        if db.users.find_one({"username": spec["username"]}):
            print(f" - {spec['role']:<8} '{spec['username']}' already exists - skipped")
            continue
        document = {
            "username": spec["username"],
            "full_name": spec["full_name"],
            "email": spec["email"],
            "phone": spec["phone"],
            "address": spec.get("address", ""),
            "avatar_url": "",
            "password": generate_password_hash(spec["password"]),
            "role": spec["role"],
            "created_at": days_ago(120),
            "last_login": None,
        }
        if spec["role"] == "seller":
            document.update({
                "seller_status": spec["seller_status"],
                "shop_name": spec["shop_name"],
                "shop_about": spec.get("shop_about", ""),
            })
        db.users.insert_one(document)
        created += 1
        print(f"[OK] {spec['role']:<8} created - username: {spec['username']}  "
              f"password: {spec['password']}")
    return created


def seed_products(db):
    created = 0
    for index, spec in enumerate(SAMPLE_PRODUCTS):
        if db.products.find_one({"name": spec["name"]}):
            continue
        owner_username = spec.get("owner")
        owner = db.users.find_one({"username": owner_username}) if owner_username else None
        document = {k: v for k, v in spec.items() if k != "owner"}
        document.update({
            "seller_id": str(owner["_id"]) if owner else None,
            "shop_name": owner.get("shop_name") if owner else "Catalogue Store",
            "seller_name": owner.get("full_name") if owner else "Store Team",
            "availability": "Yes" if document["quantity"] > 0 else "No",
            "created_at": days_ago(40 - index),
            "created_by": owner_username or "seed",
        })
        db.products.insert_one(document)
        created += 1
    print(f"[OK] Products: {created} inserted, {db.products.count_documents({})} total")
    return created


def backfill_products(db):
    """Give older documents the fields newer features rely on."""
    touched = 0
    touched += db.products.update_many({"views": {"$exists": False}},
                                       {"$set": {"views": 0}}).modified_count
    touched += db.products.update_many({"seller_id": {"$exists": False}},
                                       {"$set": {"seller_id": None}}).modified_count
    touched += db.products.update_many(
        {"$or": [{"shop_name": {"$in": [None, ""]}}, {"shop_name": {"$exists": False}}]},
        {"$set": {"shop_name": "Catalogue Store"}}).modified_count
    touched += db.products.update_many(
        {"$or": [{"seller_name": {"$in": [None, ""]}},
                  {"seller_name": {"$exists": False}}]},
        {"$set": {"seller_name": "Store Team"}}).modified_count
    touched += db.users.update_many({"address": {"$exists": False}},
                                    {"$set": {"address": ""}}).modified_count
    touched += db.users.update_many({"avatar_url": {"$exists": False}},
                                    {"$set": {"avatar_url": ""}}).modified_count

    # Hand the seller-owned demo products to their shop accounts.
    ownership = {}
    for spec in SAMPLE_PRODUCTS:
        if spec.get("owner"):
            ownership[spec["name"]] = spec["owner"]
    for name, username in ownership.items():
        owner = db.users.find_one({"username": username})
        product = db.products.find_one({"name": name})
        if not owner or not product:
            continue
        if product.get("seller_id") == str(owner["_id"]):
            continue
        db.products.update_one({"_id": product["_id"]}, {"$set": {
            "seller_id": str(owner["_id"]),
            "shop_name": owner.get("shop_name"),
            "seller_name": owner.get("full_name"),
        }})
        touched += 1

    # Repair dead third-party image URLs so no product renders a broken image.
    for dead, replacement in LEGACY_IMAGE_FIXES.items():
        touched += db.products.update_many(
            {"image_url": {"$regex": re.escape(dead.split("?")[0])}},
            {"$set": {"image_url": replacement}}).modified_count

    # Keep availability in sync with stock.
    touched += db.products.update_many(
        {"quantity": {"$gt": 0}, "availability": {"$ne": "Yes"}},
        {"$set": {"availability": "Yes"}}).modified_count
    touched += db.products.update_many(
        {"quantity": {"$lte": 0}}, {"$set": {"availability": "No"}}).modified_count
    print(f"[OK] Backfilled {touched} field(s) on existing documents")


def seed_coupons(db):
    created = 0
    for spec in SAMPLE_COUPONS:
        if db.coupons.find_one({"code": spec["code"]}):
            continue
        document = dict(spec)
        document.update({"used_count": 0, "created_at": days_ago(30)})
        db.coupons.insert_one(document)
        created += 1
    print(f"[OK] Coupons: {created} inserted, {db.coupons.count_documents({})} total")
    return created


def seed_reviews(db):
    customer = db.users.find_one({"username": "customer"})
    if not customer:
        return 0
    wanted = {
        "Fresh Mango": (5, "Very fresh and sweet. Will order again!"),
        "Cotton Shirt": (4, "Good quality fabric, slightly loose fit."),
        "Handmade Basket": (5, "Beautiful craftsmanship, looks great at home."),
        "Wireless Earbuds": (3, "Sound is good, but the case scratches easily."),
    }
    created = 0
    for name, (rating, comment) in wanted.items():
        product = db.products.find_one({"name": name})
        if not product:
            continue
        if db.reviews.find_one({"product_id": str(product["_id"]),
                                "user_id": str(customer["_id"])}):
            continue
        db.reviews.insert_one({
            "product_id": str(product["_id"]),
            "user_id": str(customer["_id"]),
            "username": customer["username"],
            "rating": rating,
            "comment": comment,
            "verified": True,
            "date": days_ago(10),
        })
        created += 1
    print(f"[OK] Reviews: {created} inserted, {db.reviews.count_documents({})} total")
    return created


def _order(db, customer, lines, status, address, when, coupon=None, discount=0.0,
           history_extra=None):
    items, subtotal = [], 0.0
    for name, qty in lines:
        product = db.products.find_one({"name": name})
        if not product:
            continue
        subtotal += product["price"] * qty
        items.append({
            "product_id": str(product["_id"]),
            "name": product["name"],
            "quantity": qty,
            "price": product["price"],
            "shop_name": product.get("shop_name", ""),
            "seller_id": product.get("seller_id"),
            "image_url": product.get("image_url", ""),
        })
    if not items:
        return 0
    total = max(subtotal - discount, 0)
    history = [{"status": "Placed", "at": when, "by": customer["username"]}]
    for extra in (history_extra or []):
        history.append({"status": extra, "at": when + datetime.timedelta(days=1),
                        "by": "admin"})
    history.append({"status": status, "at": when + datetime.timedelta(days=2), "by": "admin"})
    # De-duplicate consecutive identical statuses.
    deduped = []
    for step in history:
        if not deduped or deduped[-1]["status"] != step["status"]:
            deduped.append(step)
    db.orders.insert_one({
        "user_id": str(customer["_id"]),
        "username": customer["username"],
        "items": items,
        "subtotal": round(subtotal, 2),
        "discount": round(discount, 2),
        "coupon_code": coupon,
        "total": round(total, 2),
        "status": status,
        "payment_method": "Cash on Delivery",
        "date": when,
        "shipping_address": address,
        "status_history": deduped,
        "seeded": True,
    })
    return 1


def seed_orders(db):
    customer = db.users.find_one({"username": "customer"})
    if not customer:
        return 0
    if db.orders.count_documents({"seeded": True}) > 0:
        print(" - Sample orders already exist - skipped")
        return 0

    created = 0
    created += _order(db, customer, [("Fresh Mango", 2), ("Cotton Shirt", 1)],
                      "Delivered", "12, Gandhi Street, Coimbatore - 641001",
                      days_ago(18), coupon="WELCOME10", discount=80.0,
                      history_extra=["Processing", "Shipped"])
    created += _order(db, customer, [("Wireless Earbuds", 1)],
                      "Shipped", "12, Gandhi Street, Coimbatore - 641001",
                      days_ago(6), history_extra=["Processing"])
    created += _order(db, customer, [("Organic Rice 5kg", 1), ("Herbal Face Wash", 2)],
                      "Placed", "12, Gandhi Street, Coimbatore - 641001",
                      days_ago(1))
    print(f"[OK] Sample orders created: {created}")
    return created


def seed_wishlist(db):
    customer = db.users.find_one({"username": "customer"})
    if not customer:
        return 0
    created = 0
    for name in ("Wireless Earbuds", "Ceramic Tea Set"):
        product = db.products.find_one({"name": name})
        if not product:
            continue
        if db.wishlist.find_one({"user_id": str(customer["_id"]),
                                 "product_id": str(product["_id"])}):
            continue
        db.wishlist.insert_one({"user_id": str(customer["_id"]),
                                "product_id": str(product["_id"]),
                                "added_at": days_ago(3)})
        created += 1
    print(f"[OK] Wishlist entries: {created} inserted")
    return created


def main():
    print("=" * 64)
    print(" Local Product Catalog - database seeder")
    print(f" Connecting to: {MONGO_URI}")
    print(f" Database     : {DB_NAME}")
    print("=" * 64)

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        print("\n[X] Could not connect to MongoDB.")
        print(f"    {exc}")
        print("\n    Start MongoDB as a Windows service (Start -> Services -> MongoDB Server)")
        print("    or open MongoDB Compass and connect to mongodb://localhost:27017")
        return 1

    db = client[DB_NAME]
    create_indexes(db)
    seed_users(db)
    seed_products(db)
    backfill_products(db)
    seed_coupons(db)
    seed_reviews(db)
    seed_orders(db)
    seed_wishlist(db)

    print("=" * 64)
    print(" Demo logins")
    print("   Admin    : admin    / admin123")
    print("   Customer : customer / customer123")
    print("   User     : user     / user123")
    print("   Seller   : seller1  / seller123   (approved shop)")
    print("   Seller   : seller2  / seller123   (pending approval)")
    print(" Demo coupons: WELCOME10 (10%), SAVE50 (₹50 over ₹300), FESTIVE20 (20% over ₹500)")
    print("=" * 64)
    print(f" MongoDB Compass -> {MONGO_URI}")
    print(f"   database '{DB_NAME}': users, products, orders, reviews,")
    print("   wishlist, coupons, login_attempts")
    print("=" * 64)
    print(" Now run:  python app.py   ->  http://127.0.0.1:5000")
    return 0


if __name__ == "__main__":
    sys.exit(main())

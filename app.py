"""
Local Product Catalog - a small but complete local marketplace.
===============================================================

Stack: Flask + Jinja2 + Bootstrap 5 + MongoDB (PyMongo) inspected with Compass.

Roles
-----
* ``user``     - shopper account (browse, cart, checkout, reviews)
* ``customer`` - shopper account with a personal statistics dashboard
* ``seller``   - shop owner; manages only their own products (needs admin approval)
* ``admin``    - store operator; full access to products, orders, users, sellers,
                 coupons, reviews and analytics

Run::

    python seed.py      # demo accounts + catalogue + coupons
    python app.py       # http://127.0.0.1:5000
    python verify.py    # end-to-end checks (in a second terminal)
"""

import csv
import datetime
import io
import os
import re
import secrets
import uuid

from bson.errors import InvalidId
from bson.objectid import ObjectId
from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from pymongo import DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-key-change-me"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=8),
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,  # hard cap for uploads
)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("MONGO_DB_NAME", "local_product_catalog")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
db = client[DB_NAME]
products_collection = db["products"]
users_collection = db["users"]
reviews_collection = db["reviews"]
orders_collection = db["orders"]
wishlist_collection = db["wishlist"]
coupons_collection = db["coupons"]
attempts_collection = db["login_attempts"]

UPLOAD_DIR = os.path.join(app.static_folder, "uploads")
ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_IMAGE_BYTES = 2 * 1024 * 1024

# Roles / statuses ----------------------------------------------------------
ROLES = ("user", "customer", "seller", "admin")
SHOPPER_ROLES = ("user", "customer")
ROLE_LABELS = {
    "user": "User",
    "customer": "Customer",
    "seller": "Seller",
    "admin": "Admin",
}
ROLE_HOME = {
    "user": "account",
    "customer": "customer_dashboard",
    "seller": "seller_dashboard",
    "admin": "admin_dashboard",
}
SELLER_STATUSES = ("pending", "approved", "rejected")
ORDER_STATUSES = ("Placed", "Processing", "Shipped", "Delivered", "Cancelled")
STATUS_COLORS = {
    "Placed": "secondary",
    "Processing": "info",
    "Shipped": "primary",
    "Delivered": "success",
    "Cancelled": "danger",
}
CATEGORIES = ("Food", "Grocery", "Clothing", "Electronics", "Handmade",
              "Stationery", "Household", "Beauty", "Sports", "Other")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
MIN_PASSWORD_LENGTH = 6
PRODUCTS_PER_PAGE = 9
MAX_LOGIN_FAILURES = 5
LOCKOUT_MINUTES = 15


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def is_valid_objectid(id_str):
    """True when ``id_str`` is a usable ObjectId string."""
    try:
        ObjectId(id_str)
        return True
    except (InvalidId, TypeError):
        return False


def oid_safe(id_str):
    """ObjectId or ``None`` - never raises (used for session/URL values)."""
    try:
        return ObjectId(id_str)
    except (InvalidId, TypeError):
        return None


def oid(id_str):
    """ObjectId, or 404 when malformed."""
    value = oid_safe(id_str)
    if value is None:
        abort(404)
    return value


def safe_next(target):
    """Allow only same-site relative redirects."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


def home_for_role(role):
    return ROLE_HOME.get(role, "account")


def now():
    return datetime.datetime.utcnow()


def money(value):
    """Format a number for display (no currency symbol - templates add ₹)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def find_user_by_username(username):
    if not username:
        return None
    return users_collection.find_one(
        {"username": {"$regex": f"^{re.escape(username.strip())}$", "$options": "i"}}
    )


def current_user():
    """Signed-in user document, cached per request; never raises."""
    if "user_id" not in session:
        return None
    if not hasattr(g, "_current_user_loaded"):
        try:
            g._current_user = users_collection.find_one({"_id": oid_safe(session["user_id"])})
        except PyMongoError:
            g._current_user = None
        g._current_user_loaded = True
    return g._current_user


def is_seller_approved():
    return (session.get("role") == "seller"
            and current_user() is not None
            and current_user().get("seller_status") == "approved")


# --- decorators ------------------------------------------------------------
def login_required(*roles):
    def decorator(view):
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in first.", "warning")
                return redirect(url_for("login", next=request.path))
            if roles and session.get("role") not in roles:
                abort(403)
            return view(*args, **kwargs)

        decorated_function.__name__ = view.__name__
        return decorated_function

    return decorator


def admin_required(view):
    return login_required("admin")(view)


def seller_required(view):
    """Seller area: must be a seller AND approved by an admin."""

    @login_required("seller")
    def decorated(*args, **kwargs):
        user = current_user()
        status = (user or {}).get("seller_status")
        if status != "approved":
            flash(
                "Your seller account is "
                f"{status or 'not set up'}. An administrator must approve it before "
                "you can manage products.",
                "warning",
            )
            return redirect(url_for("seller_status"))
        return view(*args, **kwargs)

    decorated.__name__ = view.__name__
    return decorated


# --- CSRF ------------------------------------------------------------------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def verify_csrf():
    """Reject state-changing requests without a valid CSRF token."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        expected = session.get("_csrf")
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
        if not expected or not sent or not secrets.compare_digest(expected, sent):
            abort(400)


# --- uploads ---------------------------------------------------------------
def save_image(file_storage, prefix):
    """Persist an uploaded image under static/uploads. Returns (url, error)."""
    if file_storage is None or not getattr(file_storage, "filename", ""):
        return None, None
    filename = file_storage.filename.strip()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_IMAGE_EXT:
        return None, "Unsupported image type. Use PNG, JPG, GIF or WEBP."
    payload = file_storage.read()
    if not payload:
        return None, "The uploaded image was empty."
    if len(payload) > MAX_IMAGE_BYTES:
        return None, "Image is larger than 2 MB."
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = f"{prefix}_{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(UPLOAD_DIR, name), "wb") as handle:
        handle.write(payload)
    return url_for("static", filename=f"uploads/{name}"), None


# --- ratings ---------------------------------------------------------------
def ratings_for_ids(product_ids):
    """{product_id_str: (average, count)} for a batch of products."""
    ids = [str(pid) for pid in product_ids]
    if not ids:
        return {}
    pipeline = [
        {"$match": {"product_id": {"$in": ids}}},
        {"$group": {"_id": "$product_id", "avg": {"$avg": "$rating"}, "count": {"$sum": 1}}},
    ]
    return {
        row["_id"]: (round(row["avg"], 1), row["count"])
        for row in reviews_collection.aggregate(pipeline)
    }


def rating_histogram(product_id):
    """{5: n, 4: n, ...} for one product."""
    pipeline = [
        {"$match": {"product_id": product_id}},
        {"$group": {"_id": "$rating", "count": {"$sum": 1}}},
    ]
    histogram = {star: 0 for star in range(1, 6)}
    for row in reviews_collection.aggregate(pipeline):
        histogram[int(row["_id"])] = row["count"]
    return histogram


def product_ids_with_min_rating(min_rating):
    pipeline = [
        {"$group": {"_id": "$product_id", "avg": {"$avg": "$rating"}}},
        {"$match": {"avg": {"$gte": float(min_rating)}}},
    ]
    return [ObjectId(row["_id"]) for row in reviews_collection.aggregate(pipeline)
            if is_valid_objectid(row["_id"])]


# --- carts -----------------------------------------------------------------
def build_cart():
    """Resolve the session cart against the catalogue.

    Returns (items, subtotal). Lines whose product vanished are pruned.
    """
    cart = session.get("cart", {})
    items, subtotal = [], 0.0
    dirty = False
    for product_id, quantity in list(cart.items()):
        product = (products_collection.find_one({"_id": oid_safe(product_id)})
                   if is_valid_objectid(product_id) else None)
        if not product:
            del cart[product_id]
            dirty = True
            continue
        line = product["price"] * quantity
        subtotal += line
        items.append({"product": product, "quantity": quantity, "subtotal": line})
    if dirty:
        session["cart"] = cart
        session.modified = True
    return items, subtotal


def resolve_coupon(code, subtotal):
    """Validate a coupon code. Returns (coupon, discount, error)."""
    code = (code or "").strip().upper()
    if not code:
        return None, 0.0, None
    coupon = coupons_collection.find_one({"code": code})
    if not coupon:
        return None, 0.0, "That coupon code does not exist."
    if not coupon.get("active", True):
        return None, 0.0, "That coupon is no longer active."
    expires = coupon.get("expires_at")
    if expires and expires < now():
        return None, 0.0, "That coupon has expired."
    if subtotal < float(coupon.get("min_order", 0) or 0):
        return None, 0.0, (
            f"Minimum order for this coupon is ₹{money(coupon.get('min_order'))}."
        )
    max_uses = int(coupon.get("max_uses", 0) or 0)
    if max_uses and int(coupon.get("used_count", 0)) >= max_uses:
        return None, 0.0, "That coupon has reached its usage limit."

    if coupon.get("kind") == "percent":
        discount = subtotal * float(coupon.get("value", 0)) / 100.0
    else:
        discount = float(coupon.get("value", 0))
    discount = min(discount, subtotal)
    return coupon, round(discount, 2), None


def cart_with_coupon():
    """(items, subtotal, coupon, discount, total, coupon_error)."""
    items, subtotal = build_cart()
    code = session.get("coupon")
    coupon, discount, error = resolve_coupon(code, subtotal)
    if error and code:
        session.pop("coupon", None)
    total = max(subtotal - discount, 0)
    return items, subtotal, coupon, discount, total, error


def new_order_history(status, who):
    return [{"status": status, "at": now(), "by": who}]


# --- navigation badges -----------------------------------------------------
def wishlist_count():
    if "user_id" not in session:
        return 0
    try:
        return wishlist_collection.count_documents({"user_id": session["user_id"]})
    except PyMongoError:
        return 0


def pending_admin_count():
    """New orders + pending seller applications shown as an admin nav badge."""
    if session.get("role") != "admin":
        return 0
    try:
        return (orders_collection.count_documents({"status": "Placed"})
                + users_collection.count_documents({"seller_status": "pending"}))
    except PyMongoError:
        return 0


@app.context_processor
def inject_globals():
    return {
        "ROLE_LABELS": ROLE_LABELS,
        "ORDER_STATUSES": ORDER_STATUSES,
        "STATUS_COLORS": STATUS_COLORS,
        "CATEGORIES": CATEGORIES,
        "money": money,
        "current_user": current_user(),
        "cart_count": len(session.get("cart", {})),
        "wishlist_count": wishlist_count(),
        "admin_alerts": pending_admin_count(),
        "seller_approved": is_seller_approved(),
    }


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for(home_for_role(session.get("role"))))

    form = {"username": "", "full_name": "", "email": "", "role": "customer",
            "shop_name": ""}

    if request.method == "POST":
        raw_role = request.form.get("role", "customer")
        form = {
            "username": (request.form.get("username") or "").strip(),
            "full_name": (request.form.get("full_name") or "").strip(),
            "email": (request.form.get("email") or "").strip().lower(),
            "role": "customer" if raw_role == "user" else raw_role,
            "shop_name": (request.form.get("shop_name") or "").strip(),
        }
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        errors = []
        if not USERNAME_RE.match(form["username"]):
            errors.append("Username must be 3-20 characters (letters, numbers, dot "
                          "or underscore).")
        if form["email"] and not EMAIL_RE.match(form["email"]):
            errors.append("Please enter a valid email address.")
        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if password != confirm:
            errors.append("Passwords do not match.")
        # Admin accounts are created by seeding or promoted by an existing admin.
        if form["role"] not in ("customer", "seller"):
            errors.append("Please choose a valid account type.")
        if form["role"] == "seller" and not form["shop_name"]:
            errors.append("Sellers must provide a shop name.")
        if not errors and find_user_by_username(form["username"]):
            errors.append("That username is already taken.")

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template("register.html", form=form)

        document = {
            "username": form["username"],
            "full_name": form["full_name"] or form["username"],
            "email": form["email"],
            "password": generate_password_hash(password),
            "role": form["role"],
            "phone": "",
            "address": "",
            "avatar_url": "",
            "created_at": now(),
            "last_login": None,
        }
        if form["role"] == "seller":
            document["seller_status"] = "pending"
            document["shop_name"] = form["shop_name"]
            document["shop_about"] = ""
        try:
            users_collection.insert_one(document)
        except DuplicateKeyError:
            flash("That username is already taken.", "danger")
            return render_template("register.html", form=form)

        if form["role"] == "seller":
            flash("Seller account created. An administrator must approve your shop "
                  "before you can add products.", "info")
        else:
            flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form=form)


def _throttle_key(username):
    return f"{(username or '').strip().lower()}|{request.remote_addr or 'local'}"


def login_blocked(username):
    """Seconds remaining on a lockout, or 0."""
    record = attempts_collection.find_one({"key": _throttle_key(username)})
    if not record or not record.get("locked_until"):
        return 0
    remaining = (record["locked_until"] - now()).total_seconds()
    return int(remaining) if remaining > 0 else 0


def register_failure(username):
    key = _throttle_key(username)
    attempts_collection.update_one(
        {"key": key},
        {"$inc": {"failures": 1}, "$set": {"updated_at": now()}},
        upsert=True,
    )
    record = attempts_collection.find_one({"key": key})
    if record and record.get("failures", 0) >= MAX_LOGIN_FAILURES:
        attempts_collection.update_one(
            {"key": key},
            {"$set": {"locked_until": now() + datetime.timedelta(minutes=LOCKOUT_MINUTES),
                      "failures": 0}},
        )


def clear_failures(username):
    attempts_collection.delete_one({"key": _throttle_key(username)})


def _authenticate(username, password, expected_role=None):
    """Validate credentials -> (user, error_message)."""
    user = find_user_by_username(username)
    if not user or not check_password_hash(user.get("password", ""), password):
        register_failure(username)
        return None, "Invalid username or password."
    role = user.get("role", "customer")
    if role not in ROLES:
        return None, "This account has an invalid role. Contact the administrator."
    if expected_role and role != expected_role:
        return None, (
            f"Those credentials belong to a {ROLE_LABELS[role]} account. "
            f"Please use the {ROLE_LABELS[role]} login."
        )
    clear_failures(username)
    return user, None


def _start_session(user, remember=False):
    session.clear()
    session.permanent = bool(remember)
    session["user_id"] = str(user["_id"])
    session["username"] = user["username"]
    session["role"] = user.get("role", "customer")
    session["_csrf"] = secrets.token_urlsafe(32)
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"last_login": now()}})


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for(home_for_role(session.get("role"))))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        remember = request.form.get("remember") == "on"

        locked = login_blocked(username)
        if locked:
            flash(f"Too many failed attempts. Try again in {locked // 60 + 1} minute(s).",
                  "danger")
            return render_template("login.html", username=username)

        user, error = _authenticate(username, password)
        if error:
            flash(error, "danger")
            return render_template("login.html", username=username)

        _start_session(user, remember)
        flash(f"Welcome back, {user['username']}!", "success")
        # Sellers that are still waiting for approval go straight to the status page.
        if user["role"] == "seller" and user.get("seller_status") != "approved":
            target = url_for("seller_status")
        else:
            target = url_for(home_for_role(user["role"]))
        return redirect(safe_next(request.args.get("next")) or target)

    return render_template("login.html", username="")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        abort(403)

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        locked = login_blocked(username)
        if locked:
            flash(f"Too many failed attempts. Try again in {locked // 60 + 1} minute(s).",
                  "danger")
            return render_template("admin_login.html", username=username)

        user, error = _authenticate(username, password, expected_role="admin")
        if error:
            flash(error, "danger")
            return render_template("admin_login.html", username=username)

        _start_session(user)
        flash(f"Signed in as administrator ({user['username']}).", "success")
        return redirect(safe_next(request.args.get("next")) or url_for("admin_dashboard"))

    return render_template("admin_login.html", username="")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------
@app.route("/account", methods=["GET", "POST"])
@login_required(*SHOPPER_ROLES, "seller")
def account():
    user = current_user()
    if user is None:
        session.clear()
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action", "profile")

        if action == "profile":
            full_name = (request.form.get("full_name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            phone = (request.form.get("phone") or "").strip()
            address = (request.form.get("address") or "").strip()
            updates = {"full_name": full_name or user["username"], "email": email,
                       "phone": phone, "address": address}
            if user.get("role") == "seller":
                updates["shop_name"] = (request.form.get("shop_name")
                                        or user.get("shop_name") or "").strip()
                updates["shop_about"] = (request.form.get("shop_about") or "").strip()

            avatar_url, upload_error = save_image(request.files.get("avatar"), "avatar")
            if upload_error:
                flash(upload_error, "danger")
            if avatar_url:
                updates["avatar_url"] = avatar_url

            if email and not EMAIL_RE.match(email):
                flash("Please enter a valid email address.", "danger")
            else:
                users_collection.update_one({"_id": user["_id"]}, {"$set": updates})
                flash("Profile updated successfully.", "success")
            return redirect(url_for("account"))

        if action == "password":
            current = request.form.get("current_password") or ""
            new = request.form.get("new_password") or ""
            confirm = request.form.get("confirm_password") or ""
            if not check_password_hash(user.get("password", ""), current):
                flash("Your current password is incorrect.", "danger")
            elif len(new) < MIN_PASSWORD_LENGTH:
                flash(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.",
                      "danger")
            elif new != confirm:
                flash("The new passwords do not match.", "danger")
            else:
                users_collection.update_one({"_id": user["_id"]},
                                            {"$set": {"password": generate_password_hash(new)}})
                flash("Password changed successfully.", "success")
            return redirect(url_for("account"))

    order_count = orders_collection.count_documents({"user_id": session["user_id"]})
    return render_template("account.html", user=user, order_count=order_count)


@app.route("/customer/dashboard")
@login_required("customer", "seller", "admin")
def customer_dashboard():
    uid = session["user_id"]
    my_orders = list(orders_collection.find({"user_id": uid}).sort([("date", DESCENDING)]))
    total_spent = sum(order.get("total", 0) for order in my_orders)
    review_count = reviews_collection.count_documents({"user_id": uid})
    status_stats = list(orders_collection.aggregate([
        {"$match": {"user_id": uid}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]))
    return render_template(
        "customer_dashboard.html",
        orders=my_orders[:5],
        order_count=len(my_orders),
        total_spent=money(total_spent),
        review_count=review_count,
        status_stats=status_stats,
    )


# ---------------------------------------------------------------------------
# Public catalogue
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    trending = list(products_collection.find({"availability": "Yes"})
                    .sort([("views", DESCENDING), ("created_at", DESCENDING)]).limit(4))
    recent = list(products_collection.find().sort([("created_at", DESCENDING)]).limit(4))
    recent_ids = session.get("recent", [])
    recently_viewed = []
    if recent_ids:
        docs = {str(p["_id"]): p for p in
                products_collection.find({"_id": {"$in": [oid_safe(i) for i in recent_ids
                                                          if is_valid_objectid(i)]}})}
        recently_viewed = [docs[i] for i in recent_ids if i in docs][:4]
    stats = {
        "products": products_collection.count_documents({}),
        "shops": len([s for s in products_collection.distinct("shop_name") if s]),
        "sellers": users_collection.count_documents({"role": "seller",
                                                       "seller_status": "approved"}),
        "orders": orders_collection.count_documents({}),
    }
    return render_template("index.html", trending=trending, recent=recent,
                           recently_viewed=recently_viewed, stats=stats,
                           featured_products=trending, total_products=stats["products"],
                           total_shops=stats["shops"])


@app.route("/products")
def products():
    query = {}
    search = (request.args.get("search") or "").strip()
    if search:
        query["$or"] = [
            {"name": {"$regex": re.escape(search), "$options": "i"}},
            {"category": {"$regex": re.escape(search), "$options": "i"}},
            {"shop_name": {"$regex": re.escape(search), "$options": "i"}},
            {"description": {"$regex": re.escape(search), "$options": "i"}},
        ]

    category = request.args.get("category")
    if category:
        query["category"] = category

    shop = request.args.get("shop")
    if shop:
        query["shop_name"] = shop

    if request.args.get("in_stock") == "1":
        query["availability"] = "Yes"

    price_filter = {}
    price_min = request.args.get("price_min")
    price_max = request.args.get("price_max")
    try:
        if price_min not in (None, ""):
            price_filter["$gte"] = float(price_min)
        if price_max not in (None, ""):
            price_filter["$lte"] = float(price_max)
    except ValueError:
        flash("Price filters must be numbers.", "warning")
    if price_filter:
        query["price"] = price_filter

    min_rating = request.args.get("min_rating")
    if min_rating:
        ids = product_ids_with_min_rating(min_rating)
        query["_id"] = {"$in": ids}

    sort_option = request.args.get("sort")
    sort_order = [("created_at", DESCENDING)]
    if sort_option == "price_low":
        sort_order = [("price", 1)]
    elif sort_option == "price_high":
        sort_order = [("price", -1)]
    elif sort_option == "name":
        sort_order = [("name", 1)]
    elif sort_option == "rating":
        sort_order = [("views", DESCENDING)]
    elif sort_option == "popular":
        sort_order = [("views", DESCENDING)]

    total = products_collection.count_documents(query)
    pages = max(1, -(-total // PRODUCTS_PER_PAGE))
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    page = min(page, pages)

    product_list = list(
        products_collection.find(query)
        .sort(sort_order)
        .skip((page - 1) * PRODUCTS_PER_PAGE)
        .limit(PRODUCTS_PER_PAGE)
    )

    wishlist_ids = set()
    if "user_id" in session:
        wishlist_ids = {row["product_id"] for row in
                        wishlist_collection.find({"user_id": session["user_id"]},
                                                 {"product_id": 1})}

    return render_template(
        "products.html",
        products=product_list,
        categories=products_collection.distinct("category"),
        shops=sorted([s for s in products_collection.distinct("shop_name") if s]),
        ratings=ratings_for_ids([p["_id"] for p in product_list]),
        page=page,
        pages=pages,
        total=total,
        wishlist_ids=wishlist_ids,
    )


@app.route("/product/<product_id>")
def product_details(product_id):
    product = (products_collection.find_one({"_id": oid_safe(product_id)})
               if is_valid_objectid(product_id) else None)
    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("products"))

    # Track popularity once per session, and keep a "recently viewed" list.
    recent = session.get("recent", [])
    if product_id not in recent:
        products_collection.update_one({"_id": product["_id"]}, {"$inc": {"views": 1}})
        recent.insert(0, product_id)
        session["recent"] = recent[:8]

    reviews = list(reviews_collection.find({"product_id": product_id})
                   .sort([("date", DESCENDING)]))
    histogram = rating_histogram(product_id)
    average = (round(sum(r["rating"] for r in reviews) / len(reviews), 1)
               if reviews else 0)

    related = list(products_collection.find(
        {"category": product.get("category"), "_id": {"$ne": product["_id"]}}
    ).limit(4))

    my_review = None
    in_wishlist = False
    can_review = False
    if "user_id" in session:
        my_review = reviews_collection.find_one({"product_id": product_id,
                                                 "user_id": session["user_id"]})
        in_wishlist = wishlist_collection.find_one({"user_id": session["user_id"],
                                                    "product_id": product_id}) is not None
        can_review = session.get("role") in SHOPPER_ROLES or session.get("role") == "seller"

    return render_template(
        "product_details.html",
        product=product,
        reviews=reviews,
        avg_rating=average,
        histogram=histogram,
        related=related,
        my_review=my_review,
        in_wishlist=in_wishlist,
        can_review=can_review,
    )


@app.route("/product/<product_id>/review", methods=["POST"])
@login_required(*SHOPPER_ROLES, "seller")
def add_review(product_id):
    product = (products_collection.find_one({"_id": oid_safe(product_id)})
               if is_valid_objectid(product_id) else None)
    if not product:
        flash("That product no longer exists.", "danger")
        return redirect(url_for("products"))

    try:
        rating = int(request.form.get("rating", 0))
    except (TypeError, ValueError):
        rating = 0
    comment = (request.form.get("comment") or "").strip()

    if rating not in (1, 2, 3, 4, 5):
        flash("Please choose a rating between 1 and 5.", "warning")
        return redirect(url_for("product_details", product_id=product_id))
    if not comment:
        flash("Please write a short comment.", "warning")
        return redirect(url_for("product_details", product_id=product_id))

    # "Verified purchase" = the shopper has an order containing this product.
    purchased = orders_collection.find_one({
        "user_id": session["user_id"],
        "status": {"$ne": "Cancelled"},
        "items.product_id": product_id,
    }) is not None

    reviews_collection.update_one(
        {"product_id": product_id, "user_id": session["user_id"]},
        {"$set": {"username": session.get("username"), "rating": rating,
                  "comment": comment, "verified": purchased, "updated_at": now()},
         "$setOnInsert": {"date": now()}},
        upsert=True,
    )
    flash("Review saved. Thank you!" + (" Marked as a verified purchase."
                                        if purchased else ""), "success")
    return redirect(url_for("product_details", product_id=product_id))


@app.route("/product/<product_id>/review/delete", methods=["POST"])
@login_required(*SHOPPER_ROLES, "seller", "admin")
def delete_review(product_id):
    query = {"product_id": product_id}
    if session.get("role") != "admin":
        query["user_id"] = session["user_id"]
    result = reviews_collection.delete_many(query)
    flash("Review deleted." if result.deleted_count else "No review to delete.",
          "success" if result.deleted_count else "warning")
    return redirect(url_for("product_details", product_id=product_id))


# ---------------------------------------------------------------------------
# Wishlist
# ---------------------------------------------------------------------------
@app.route("/wishlist")
@login_required(*SHOPPER_ROLES, "seller")
def wishlist():
    rows = list(wishlist_collection.find({"user_id": session["user_id"]})
                .sort([("added_at", DESCENDING)]))
    ids = [oid_safe(r["product_id"]) for r in rows if is_valid_objectid(r["product_id"])]
    docs = {str(p["_id"]): p for p in products_collection.find({"_id": {"$in": ids}})}
    items = [docs[r["product_id"]] for r in rows if r["product_id"] in docs]
    return render_template("wishlist.html", items=items,
                           ratings=ratings_for_ids(list(docs.keys())))


@app.route("/wishlist/toggle/<product_id>", methods=["POST"])
@login_required(*SHOPPER_ROLES, "seller")
def toggle_wishlist(product_id):
    if not is_valid_objectid(product_id):
        abort(404)
    existing = wishlist_collection.find_one({"user_id": session["user_id"],
                                             "product_id": product_id})
    if existing:
        wishlist_collection.delete_one({"_id": existing["_id"]})
        flash("Removed from your wishlist.", "info")
    else:
        if not products_collection.find_one({"_id": ObjectId(product_id)}):
            abort(404)
        wishlist_collection.insert_one({"user_id": session["user_id"],
                                        "product_id": product_id, "added_at": now()})
        flash("Added to your wishlist.", "success")
    return redirect(request.referrer or url_for("wishlist"))


# ---------------------------------------------------------------------------
# Cart & checkout
# ---------------------------------------------------------------------------
@app.route("/cart")
def view_cart():
    items, subtotal, coupon, discount, total, coupon_error = cart_with_coupon()
    if coupon_error:
        flash(coupon_error, "warning")
    session["cart_total"] = total
    return render_template("cart.html", cart_items=items, subtotal=money(subtotal),
                           coupon=coupon, discount=money(discount),
                           total=money(total))


@app.route("/cart/add/<product_id>", methods=["POST"])
def add_to_cart(product_id):
    product = (products_collection.find_one({"_id": oid_safe(product_id)})
               if is_valid_objectid(product_id) else None)
    if not product:
        flash("That product no longer exists.", "danger")
        return redirect(url_for("products"))
    if product.get("availability") != "Yes" or product.get("quantity", 0) <= 0:
        flash("Product is currently unavailable or out of stock.", "danger")
        return redirect(request.referrer or url_for("products"))

    try:
        qty = max(1, int(request.form.get("quantity", 1)))
    except (TypeError, ValueError):
        qty = 1

    cart = session.get("cart", {})
    current = cart.get(product_id, 0)
    if current + qty > product["quantity"]:
        flash(f"Cannot add {qty} more. Only {product['quantity']} left in stock.",
              "warning")
        return redirect(request.referrer or url_for("products"))

    cart[product_id] = current + qty
    session["cart"] = cart
    session.modified = True
    flash("Item added to cart!", "success")
    return redirect(request.referrer or url_for("products"))


@app.route("/cart/update/<product_id>", methods=["POST"])
def update_cart(product_id):
    cart = session.get("cart", {})
    if product_id not in cart:
        return redirect(url_for("view_cart"))
    product = (products_collection.find_one({"_id": oid_safe(product_id)})
               if is_valid_objectid(product_id) else None)
    if not product:
        del cart[product_id]
        session["cart"] = cart
        session.modified = True
        flash("That product no longer exists.", "danger")
        return redirect(url_for("view_cart"))

    try:
        qty = int(request.form.get("quantity", 1))
    except (TypeError, ValueError):
        qty = 1
    if qty <= 0:
        del cart[product_id]
        flash("Item removed from cart.", "info")
    elif qty > product["quantity"]:
        cart[product_id] = product["quantity"]
        flash(f"Only {product['quantity']} available - quantity adjusted.", "warning")
    else:
        cart[product_id] = qty
        flash("Cart updated.", "success")
    session["cart"] = cart
    session.modified = True
    return redirect(url_for("view_cart"))


@app.route("/cart/remove/<product_id>")
def remove_from_cart(product_id):
    cart = session.get("cart", {})
    if product_id in cart:
        del cart[product_id]
        session["cart"] = cart
        session.modified = True
        flash("Item removed from cart.", "info")
    return redirect(url_for("view_cart"))


@app.route("/cart/coupon", methods=["POST"])
def apply_coupon():
    code = (request.form.get("code") or "").strip().upper()
    items, subtotal = build_cart()
    coupon, discount, error = resolve_coupon(code, subtotal)
    if error:
        session.pop("coupon", None)
        flash(error, "danger")
    else:
        session["coupon"] = coupon["code"]
        flash(f"Coupon {coupon['code']} applied - you save ₹{money(discount)}!", "success")
    return redirect(url_for("view_cart"))


@app.route("/cart/coupon/remove", methods=["POST"])
def remove_coupon():
    session.pop("coupon", None)
    flash("Coupon removed.", "info")
    return redirect(url_for("view_cart"))


@app.route("/checkout", methods=["GET", "POST"])
@login_required(*SHOPPER_ROLES, "seller")
def checkout():
    items, subtotal, coupon, discount, total, coupon_error = cart_with_coupon()
    if coupon_error:
        flash(coupon_error, "warning")
    if not items:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("products"))

    user = current_user()
    if request.method == "POST":
        address = (request.form.get("address") or "").strip()
        payment = request.form.get("payment_method", "Cash on Delivery")
        if not address:
            flash("A shipping address is required.", "danger")
            return render_template("checkout.html", items=items, subtotal=money(subtotal),
                                   coupon=coupon, discount=money(discount),
                                   total=money(total), user=user)

        # Re-check stock right before committing.
        for item in items:
            if item["quantity"] > item["product"].get("quantity", 0):
                flash(f"Only {item['product'].get('quantity', 0)} × "
                      f"{item['product']['name']} left in stock.", "danger")
                return redirect(url_for("view_cart"))

        order_items = []
        for item in items:
            product, qty = item["product"], item["quantity"]
            order_items.append({
                "product_id": str(product["_id"]),
                "name": product["name"],
                "quantity": qty,
                "price": product["price"],
                "shop_name": product.get("shop_name", ""),
                "seller_id": product.get("seller_id"),
                "image_url": product.get("image_url", ""),
            })
            products_collection.update_one({"_id": product["_id"]},
                                            {"$inc": {"quantity": -qty}})
            products_collection.update_one({"_id": product["_id"], "quantity": {"$lte": 0}},
                                            {"$set": {"availability": "No"}})

        order = {
            "user_id": session["user_id"],
            "username": session.get("username"),
            "items": order_items,
            "subtotal": round(subtotal, 2),
            "discount": round(discount, 2),
            "coupon_code": coupon["code"] if coupon else None,
            "total": round(total, 2),
            "status": "Placed",
            "payment_method": payment,
            "date": now(),
            "shipping_address": address,
            "status_history": new_order_history("Placed", session.get("username")),
        }
        result = orders_collection.insert_one(order)

        if coupon:
            coupons_collection.update_one({"_id": coupon["_id"]}, {"$inc": {"used_count": 1}})
        session.pop("cart", None)
        session.pop("coupon", None)
        flash("Order placed successfully!", "success")
        return redirect(url_for("order_detail", order_id=str(result.inserted_id)))

    return render_template("checkout.html", items=items, subtotal=money(subtotal),
                           coupon=coupon, discount=money(discount), total=money(total),
                           user=user)


# ---------------------------------------------------------------------------
# Orders (shopper view)
# ---------------------------------------------------------------------------
def _load_order_for_viewer(order_id):
    """Fetch an order if the viewer owns it or is an admin."""
    order = (orders_collection.find_one({"_id": oid_safe(order_id)})
             if is_valid_objectid(order_id) else None)
    if not order:
        abort(404)
    if session.get("role") != "admin" and order.get("user_id") != session.get("user_id"):
        abort(403)
    return order


@app.route("/orders")
@login_required(*SHOPPER_ROLES, "seller")
def my_orders():
    orders = list(orders_collection.find({"user_id": session["user_id"]})
                  .sort([("date", DESCENDING)]))
    return render_template("my_orders.html", orders=orders)


@app.route("/orders/<order_id>")
@login_required(*SHOPPER_ROLES, "seller", "admin")
def order_detail(order_id):
    order = _load_order_for_viewer(order_id)
    history = sorted(order.get("status_history", []),
                     key=lambda row: row.get("at") or now())
    return render_template("order_detail.html", order=order, history=history,
                           is_admin=session.get("role") == "admin",
                           can_cancel=(order.get("status") in ("Placed", "Processing")
                                       and order.get("user_id") == session.get("user_id")))


@app.route("/orders/<order_id>/invoice")
@login_required(*SHOPPER_ROLES, "seller", "admin")
def order_invoice(order_id):
    order = _load_order_for_viewer(order_id)
    return render_template("invoice.html", order=order)


@app.route("/orders/<order_id>/cancel", methods=["POST"])
@login_required(*SHOPPER_ROLES, "seller")
def cancel_order(order_id):
    order = _load_order_for_viewer(order_id)
    if order.get("status") not in ("Placed", "Processing"):
        flash("This order can no longer be cancelled - contact support.", "warning")
        return redirect(url_for("order_detail", order_id=order_id))

    for item in order.get("items", []):
        product_id = oid_safe(item.get("product_id"))
        if product_id is None:
            continue
        products_collection.update_one({"_id": product_id},
                                        {"$inc": {"quantity": item.get("quantity", 0)},
                                         "$set": {"availability": "Yes"}})

    orders_collection.update_one(
        {"_id": order["_id"]},
        {"$set": {"status": "Cancelled", "cancelled_at": now()},
         "$push": {"status_history": {"status": "Cancelled", "at": now(),
                                      "by": session.get("username")}}},
    )
    flash("Order cancelled and stock restored.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/orders/<order_id>/reorder", methods=["POST"])
@login_required(*SHOPPER_ROLES, "seller")
def reorder(order_id):
    order = _load_order_for_viewer(order_id)
    cart = session.get("cart", {})
    added, skipped = 0, 0
    for item in order.get("items", []):
        pid = item.get("product_id")
        product = (products_collection.find_one({"_id": oid_safe(pid)})
                   if is_valid_objectid(pid) else None)
        if not product or product.get("availability") != "Yes":
            skipped += 1
            continue
        wanted = min(item.get("quantity", 1), product.get("quantity", 0))
        if wanted <= 0:
            skipped += 1
            continue
        cart[pid] = wanted
        added += 1
    session["cart"] = cart
    session.modified = True
    if added:
        flash(f"{added} item(s) added back to your cart."
              + (f" {skipped} unavailable." if skipped else ""), "success")
        return redirect(url_for("view_cart"))
    flash("None of those items are available right now.", "warning")
    return redirect(url_for("order_detail", order_id=order_id))


# ---------------------------------------------------------------------------
# Seller area
# ---------------------------------------------------------------------------
@app.route("/seller/status")
@login_required("seller")
def seller_status():
    user = current_user()
    return render_template("seller_status.html", user=user,
                           status=(user or {}).get("seller_status", "pending"))


def _seller_product_or_404(product_id):
    product = (products_collection.find_one({"_id": oid_safe(product_id)})
               if is_valid_objectid(product_id) else None)
    if not product or product.get("seller_id") != session["user_id"]:
        abort(404)
    return product


@app.route("/seller")
@seller_required
def seller_dashboard():
    user = current_user()
    mine = list(products_collection.find({"seller_id": session["user_id"]})
                .sort([("created_at", DESCENDING)]))
    pipeline = [
        {"$match": {"status": {"$ne": "Cancelled"}, "items.seller_id": session["user_id"]}},
        {"$unwind": "$items"},
        {"$match": {"items.seller_id": session["user_id"]}},
        {"$group": {"_id": None, "units": {"$sum": "$items.quantity"},
                    "revenue": {"$sum": {"$multiply": ["$items.price",
                                                       "$items.quantity"]}}}},
    ]
    totals = list(orders_collection.aggregate(pipeline))
    units = totals[0]["units"] if totals else 0
    revenue = totals[0]["revenue"] if totals else 0
    order_count = orders_collection.count_documents(
        {"items.seller_id": session["user_id"], "status": {"$ne": "Cancelled"}})
    low_stock = [p for p in mine if p.get("quantity", 0) <= 5]

    return render_template("seller_dashboard.html", user=user, products=mine,
                           units=units, revenue=money(revenue), order_count=order_count,
                           low_stock=low_stock)


@app.route("/seller/products")
@seller_required
def seller_products():
    mine = list(products_collection.find({"seller_id": session["user_id"]})
                .sort([("created_at", DESCENDING)]))
    return render_template("seller_products.html", products=mine,
                           ratings=ratings_for_ids([p["_id"] for p in mine]))


@app.route("/seller/products/add", methods=["GET", "POST"])
@seller_required
def seller_add_product():
    form = {"category": "Other", "availability": "Yes"}
    if request.method == "POST":
        data, errors = _product_form_data()
        image_url, upload_error = save_image(request.files.get("image"), "product")
        if upload_error:
            errors.append(upload_error)
        if image_url:
            data["image_url"] = image_url
        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template("seller_product_form.html", form=data, product=None)

        user = current_user()
        data.update({
            "seller_id": session["user_id"],
            "shop_name": data.get("shop_name") or user.get("shop_name") or user["username"],
            "seller_name": data.get("seller_name") or user.get("full_name"),
            "created_at": now(),
            "views": 0,
        })
        products_collection.insert_one(data)
        flash("Product published to your shop.", "success")
        return redirect(url_for("seller_products"))

    return render_template("seller_product_form.html", form=form, product=None)


@app.route("/seller/products/edit/<product_id>", methods=["GET", "POST"])
@seller_required
def seller_edit_product(product_id):
    product = _seller_product_or_404(product_id)
    if request.method == "POST":
        data, errors = _product_form_data()
        image_url, upload_error = save_image(request.files.get("image"), "product")
        if upload_error:
            errors.append(upload_error)
        if image_url:
            data["image_url"] = image_url
        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template("seller_product_form.html", form=product, product=product)

        data["updated_at"] = now()
        products_collection.update_one({"_id": product["_id"]}, {"$set": data})
        flash("Product updated.", "success")
        return redirect(url_for("seller_products"))

    return render_template("seller_product_form.html", form=product, product=product)


@app.route("/seller/products/delete/<product_id>", methods=["POST"])
@seller_required
def seller_delete_product(product_id):
    product = _seller_product_or_404(product_id)
    products_collection.delete_one({"_id": product["_id"]})
    reviews_collection.delete_many({"product_id": product_id})
    wishlist_collection.delete_many({"product_id": product_id})
    flash("Product deleted.", "success")
    return redirect(url_for("seller_products"))


@app.route("/seller/orders")
@seller_required
def seller_orders():
    orders = list(orders_collection.find({"items.seller_id": session["user_id"]})
                  .sort([("date", DESCENDING)]))
    rows = []
    for order in orders:
        mine = [i for i in order.get("items", [])
                if i.get("seller_id") == session["user_id"]]
        if not mine:
            continue
        revenue = sum(i["price"] * i["quantity"] for i in mine)
        rows.append({"order": order, "items": mine, "revenue": revenue})
    return render_template("seller_orders.html", rows=rows)


# ---------------------------------------------------------------------------
# Admin area - catalogue
# ---------------------------------------------------------------------------
def _product_form_data():
    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "Other").strip()
    description = (request.form.get("description") or "").strip()
    try:
        price = float(request.form.get("price") or 0)
    except ValueError:
        price = -1
    try:
        quantity = int(request.form.get("quantity") or 0)
    except ValueError:
        quantity = -1

    errors = []
    if not name:
        errors.append("Product name is required.")
    if price < 0:
        errors.append("Price must be a positive number.")
    if quantity < 0:
        errors.append("Quantity must be zero or more.")

    data = {
        "name": name,
        "category": category if category in CATEGORIES else "Other",
        "description": description,
        "price": max(price, 0),
        "quantity": max(quantity, 0),
        "seller_name": (request.form.get("seller_name") or "").strip(),
        "shop_name": (request.form.get("shop_name") or "").strip(),
        "location": (request.form.get("location") or "").strip(),
        "phone": (request.form.get("phone") or "").strip(),
        "image_url": (request.form.get("image_url") or "").strip()
        or "https://via.placeholder.com/600x400?text=No+Image",
        "availability": "Yes" if request.form.get("availability") != "No" else "No",
    }
    return data, errors


@app.route("/admin")
@admin_required
def admin_dashboard():
    totals = {
        "products": products_collection.count_documents({}),
        "available": products_collection.count_documents({"availability": "Yes"}),
        "out_of_stock": products_collection.count_documents({"availability": "No"}),
        "categories": len(products_collection.distinct("category")),
        "users": users_collection.count_documents({}),
        "sellers": users_collection.count_documents({"role": "seller",
                                                      "seller_status": "approved"}),
        "pending_sellers": users_collection.count_documents({"seller_status": "pending"}),
        "orders": orders_collection.count_documents({}),
        "reviews": reviews_collection.count_documents({}),
        "wishlist": wishlist_collection.count_documents({}),
    }

    revenue_rows = list(orders_collection.aggregate([
        {"$match": {"status": {"$ne": "Cancelled"}}},
        {"$group": {"_id": None, "revenue": {"$sum": "$total"},
                    "discount": {"$sum": "$discount"}}},
    ]))
    totals["revenue"] = revenue_rows[0]["revenue"] if revenue_rows else 0
    totals["discount"] = revenue_rows[0]["discount"] if revenue_rows else 0

    category_stats = list(products_collection.aggregate([
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]))
    role_stats = {row["_id"]: row["count"] for row in users_collection.aggregate([
        {"$group": {"_id": "$role", "count": {"$sum": 1}}}])}
    status_stats = {row["_id"]: row["count"] for row in orders_collection.aggregate([
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}])}

    sales_by_day = list(orders_collection.aggregate([
        {"$match": {"status": {"$ne": "Cancelled"}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}},
                    "revenue": {"$sum": "$total"}, "orders": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
        {"$limit": 14},
    ]))

    top_products = list(orders_collection.aggregate([
        {"$match": {"status": {"$ne": "Cancelled"}}},
        {"$unwind": "$items"},
        {"$group": {"_id": "$items.name",
                    "units": {"$sum": "$items.quantity"},
                    "revenue": {"$sum": {"$multiply": ["$items.price", "$items.quantity"]}}}},
        {"$sort": {"units": -1}},
        {"$limit": 5},
    ]))

    top_shops = list(orders_collection.aggregate([
        {"$match": {"status": {"$ne": "Cancelled"}}},
        {"$unwind": "$items"},
        {"$match": {"items.shop_name": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$items.shop_name",
                    "revenue": {"$sum": {"$multiply": ["$items.price", "$items.quantity"]}}}},
        {"$sort": {"revenue": -1}},
        {"$limit": 5},
    ]))

    low_stock = list(products_collection.find({"quantity": {"$lte": 5}})
                     .sort([("quantity", 1)]).limit(8))
    recent_orders = list(orders_collection.find().sort([("date", DESCENDING)]).limit(6))

    return render_template(
        "admin_dashboard.html",
        totals=totals,
        products=list(products_collection.find().sort([("created_at", DESCENDING)]).limit(10)),
        chart_labels=[row["_id"] or "Uncategorised" for row in category_stats],
        chart_data=[row["count"] for row in category_stats],
        role_stats=role_stats,
        status_stats=status_stats,
        sales_days=[row["_id"] for row in sales_by_day],
        sales_revenue=[row["revenue"] for row in sales_by_day],
        top_products=top_products,
        top_shops=top_shops,
        low_stock=low_stock,
        recent_orders=recent_orders,
        # legacy names kept so older templates keep working
        total_products=totals["products"],
        available_products=totals["available"],
        out_of_stock_products=totals["out_of_stock"],
        num_categories=totals["categories"],
        total_users=totals["users"],
        total_orders=totals["orders"],
        total_revenue=money(totals["revenue"]),
    )


@app.route("/admin/products")
@admin_required
def admin_products():
    page = max(1, request.args.get("page", 1, type=int))
    search = (request.args.get("search") or "").strip()
    query = {}
    if search:
        query["name"] = {"$regex": re.escape(search), "$options": "i"}
    total = products_collection.count_documents(query)
    pages = max(1, -(-total // 20))
    page = min(page, pages)
    items = list(products_collection.find(query).sort([("created_at", DESCENDING)])
                 .skip((page - 1) * 20).limit(20))
    return render_template("admin_products.html", products=items, page=page, pages=pages,
                           total=total, search=search)


@app.route("/admin/add", methods=["GET", "POST"])
@admin_required
def add_product():
    if request.method == "POST":
        data, errors = _product_form_data()
        image_url, upload_error = save_image(request.files.get("image"), "product")
        if upload_error:
            errors.append(upload_error)
        if image_url:
            data["image_url"] = image_url
        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template("add_product.html", form=data)
        data.update({"created_at": now(), "views": 0,
                     "created_by": session.get("username")})
        products_collection.insert_one(data)
        flash("Product added successfully!", "success")
        return redirect(url_for("admin_products"))
    return render_template("add_product.html", form={})


@app.route("/admin/edit/<product_id>", methods=["GET", "POST"])
@admin_required
def edit_product(product_id):
    product = (products_collection.find_one({"_id": oid_safe(product_id)})
               if is_valid_objectid(product_id) else None)
    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("admin_products"))

    if request.method == "POST":
        data, errors = _product_form_data()
        image_url, upload_error = save_image(request.files.get("image"), "product")
        if upload_error:
            errors.append(upload_error)
        if image_url:
            data["image_url"] = image_url
        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template("edit_product.html", product=product)
        data["updated_at"] = now()
        products_collection.update_one({"_id": product["_id"]}, {"$set": data})
        flash("Product updated successfully!", "success")
        return redirect(url_for("admin_products"))

    return render_template("edit_product.html", product=product)


@app.route("/admin/delete/<product_id>", methods=["POST"])
@admin_required
def delete_product(product_id):
    if not is_valid_objectid(product_id):
        flash("Invalid product.", "danger")
        return redirect(url_for("admin_products"))
    result = products_collection.delete_one({"_id": ObjectId(product_id)})
    if result.deleted_count:
        reviews_collection.delete_many({"product_id": product_id})
        wishlist_collection.delete_many({"product_id": product_id})
        flash("Product deleted successfully!", "success")
    else:
        flash("Product not found.", "danger")
    return redirect(url_for("admin_products"))


# ---------------------------------------------------------------------------
# Admin area - orders
# ---------------------------------------------------------------------------
@app.route("/admin/orders")
@admin_required
def admin_orders():
    query = {}
    status_filter = request.args.get("status")
    if status_filter in ORDER_STATUSES:
        query["status"] = status_filter
    search = (request.args.get("search") or "").strip()
    if search:
        query["username"] = {"$regex": re.escape(search), "$options": "i"}

    orders = list(orders_collection.find(query).sort([("date", DESCENDING)]).limit(200))
    revenue = sum(order.get("total", 0) for order in orders)
    counts = {row["_id"]: row["count"] for row in orders_collection.aggregate([
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}])}
    return render_template("admin_orders.html", orders=orders, revenue=money(revenue),
                           status_filter=status_filter, search=search, counts=counts)


@app.route("/admin/orders/<order_id>/status", methods=["POST"])
@admin_required
def update_order_status(order_id):
    if not is_valid_objectid(order_id):
        flash("Invalid order.", "danger")
        return redirect(url_for("admin_orders"))
    status = request.form.get("status")
    if status not in ORDER_STATUSES:
        flash("Unknown order status.", "danger")
        return redirect(url_for("admin_orders"))

    order = orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("admin_orders"))
    if order.get("status") == status:
        flash("That is already the current status.", "info")
        return redirect(request.referrer or url_for("admin_orders"))

    # Cancelling restores stock.
    if status == "Cancelled" and order.get("status") != "Cancelled":
        for item in order.get("items", []):
            pid = oid_safe(item.get("product_id"))
            if pid is not None:
                products_collection.update_one(
                    {"_id": pid},
                    {"$inc": {"quantity": item.get("quantity", 0)},
                     "$set": {"availability": "Yes"}})

    orders_collection.update_one(
        {"_id": order["_id"]},
        {"$set": {"status": status},
         "$push": {"status_history": {"status": status, "at": now(),
                                      "by": session.get("username")}}},
    )
    flash(f"Order marked as {status}.", "success")
    return redirect(request.referrer or url_for("admin_orders"))


@app.route("/admin/orders/export")
@admin_required
def export_orders():
    query = {}
    status_filter = request.args.get("status")
    if status_filter in ORDER_STATUSES:
        query["status"] = status_filter
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Order ID", "Date", "Customer", "Items", "Subtotal", "Discount",
                     "Coupon", "Total", "Status", "Payment", "Shipping Address"])
    for order in orders_collection.find(query).sort([("date", DESCENDING)]):
        writer.writerow([
            str(order["_id"]),
            (order.get("date") or now()).strftime("%Y-%m-%d %H:%M"),
            order.get("username") or "",
            " | ".join(f"{i['quantity']}x {i['name']}" for i in order.get("items", [])),
            order.get("subtotal", order.get("total", 0)),
            order.get("discount", 0),
            order.get("coupon_code") or "",
            order.get("total", 0),
            order.get("status", ""),
            order.get("payment_method", ""),
            (order.get("shipping_address") or "").replace("\n", " "),
        ])
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename=orders-{now().strftime('%Y%m%d-%H%M')}.csv"},
    )


# ---------------------------------------------------------------------------
# Admin area - users, sellers, coupons, reviews
# ---------------------------------------------------------------------------
@app.route("/admin/users")
@admin_required
def admin_users():
    search = (request.args.get("search") or "").strip()
    query = {}
    if search:
        query["$or"] = [
            {"username": {"$regex": re.escape(search), "$options": "i"}},
            {"full_name": {"$regex": re.escape(search), "$options": "i"}},
            {"email": {"$regex": re.escape(search), "$options": "i"}},
        ]
    role_filter = request.args.get("role")
    if role_filter in ROLES:
        query["role"] = role_filter
    all_users = list(users_collection.find(query).sort([("username", 1)]))
    order_counts = {row["_id"]: row["count"] for row in orders_collection.aggregate([
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}}}])}
    return render_template("admin_users.html", users=all_users, role_filter=role_filter,
                           search=search, order_counts=order_counts,
                           role_stats={row["_id"]: row["count"] for row in
                                       users_collection.aggregate([
                                           {"$group": {"_id": "$role",
                                                       "count": {"$sum": 1}}}])})


@app.route("/admin/users/<user_id>/role", methods=["POST"])
@admin_required
def update_user_role(user_id):
    if not is_valid_objectid(user_id):
        flash("Invalid user.", "danger")
        return redirect(url_for("admin_users"))
    role = request.form.get("role")
    if role not in ROLES:
        flash("Unknown role.", "danger")
        return redirect(url_for("admin_users"))
    if user_id == session["user_id"] and role != "admin":
        flash("You cannot remove your own admin access.", "danger")
        return redirect(url_for("admin_users"))
    result = users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": {"role": role}})
    flash("User role updated." if result.matched_count else "User not found.",
          "success" if result.matched_count else "danger")
    return redirect(request.referrer or url_for("admin_users"))


@app.route("/admin/users/<user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    if not is_valid_objectid(user_id):
        flash("Invalid user.", "danger")
        return redirect(url_for("admin_users"))
    if user_id == session["user_id"]:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("admin_users"))
    user = users_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin_users"))
    if user.get("role") == "admin" and users_collection.count_documents(
            {"role": "admin"}) <= 1:
        flash("At least one admin account must remain.", "danger")
        return redirect(url_for("admin_users"))
    users_collection.delete_one({"_id": ObjectId(user_id)})
    reviews_collection.delete_many({"user_id": user_id})
    wishlist_collection.delete_many({"user_id": user_id})
    flash(f"User '{user['username']}' deleted.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/sellers")
@admin_required
def admin_sellers():
    status_filter = request.args.get("status", "pending")
    query = {"role": "seller"}
    if status_filter in SELLER_STATUSES:
        query["seller_status"] = status_filter
    sellers = list(users_collection.find(query).sort([("created_at", DESCENDING)]))
    for seller in sellers:
        seller["product_count"] = products_collection.count_documents(
            {"seller_id": str(seller["_id"])})
    counts = {row["_id"]: row["count"] for row in users_collection.aggregate([
        {"$match": {"role": "seller"}},
        {"$group": {"_id": "$seller_status", "count": {"$sum": 1}}}])}
    return render_template("admin_sellers.html", sellers=sellers, counts=counts,
                           status_filter=status_filter)


@app.route("/admin/sellers/<user_id>/<action>", methods=["POST"])
@admin_required
def moderate_seller(user_id, action):
    if action not in ("approve", "reject"):
        abort(404)
    if not is_valid_objectid(user_id):
        flash("Invalid seller.", "danger")
        return redirect(url_for("admin_sellers"))
    status = "approved" if action == "approve" else "rejected"
    result = users_collection.update_one(
        {"_id": ObjectId(user_id), "role": "seller"},
        {"$set": {"seller_status": status}},
    )
    flash(f"Seller {status}." if result.matched_count else "Seller not found.",
          "success" if result.matched_count else "danger")
    return redirect(url_for("admin_sellers"))


@app.route("/admin/coupons")
@admin_required
def admin_coupons():
    coupons = list(coupons_collection.find().sort([("created_at", DESCENDING)]))
    return render_template("admin_coupons.html", coupons=coupons)


@app.route("/admin/coupons/add", methods=["POST"])
@admin_required
def add_coupon():
    code = (request.form.get("code") or "").strip().upper()
    kind = request.form.get("kind", "percent")
    try:
        value = float(request.form.get("value") or 0)
    except ValueError:
        value = -1
    try:
        min_order = float(request.form.get("min_order") or 0)
    except ValueError:
        min_order = 0
    try:
        max_uses = int(request.form.get("max_uses") or 0)
    except ValueError:
        max_uses = 0
    expiry_raw = (request.form.get("expires_at") or "").strip()

    errors = []
    if not re.match(r"^[A-Z0-9_-]{3,20}$", code):
        errors.append("Coupon code must be 3-20 characters (A-Z, 0-9, dash, underscore).")
    if kind not in ("percent", "flat"):
        errors.append("Discount type must be percent or flat.")
    if value <= 0:
        errors.append("Discount value must be greater than zero.")
    if kind == "percent" and value > 90:
        errors.append("A percentage discount above 90% is not allowed.")
    if coupons_collection.find_one({"code": code}):
        errors.append("That coupon code already exists.")

    expires_at = None
    if expiry_raw:
        try:
            expires_at = datetime.datetime.strptime(expiry_raw, "%Y-%m-%d")
        except ValueError:
            errors.append("Expiry must look like 2026-12-31.")

    if errors:
        for message in errors:
            flash(message, "danger")
        return redirect(url_for("admin_coupons"))

    coupons_collection.insert_one({
        "code": code, "kind": kind, "value": value, "min_order": min_order,
        "max_uses": max_uses, "used_count": 0, "expires_at": expires_at,
        "active": True, "created_at": now(),
    })
    flash(f"Coupon {code} created.", "success")
    return redirect(url_for("admin_coupons"))


@app.route("/admin/coupons/<coupon_id>/toggle", methods=["POST"])
@admin_required
def toggle_coupon(coupon_id):
    coupon = (coupons_collection.find_one({"_id": oid_safe(coupon_id)})
              if is_valid_objectid(coupon_id) else None)
    if not coupon:
        flash("Coupon not found.", "danger")
        return redirect(url_for("admin_coupons"))
    new_state = not coupon.get("active", True)
    coupons_collection.update_one({"_id": coupon["_id"]}, {"$set": {"active": new_state}})
    flash(f"Coupon {coupon['code']} is now {'active' if new_state else 'disabled'}.",
          "success")
    return redirect(url_for("admin_coupons"))


@app.route("/admin/coupons/<coupon_id>/delete", methods=["POST"])
@admin_required
def delete_coupon(coupon_id):
    result = (coupons_collection.delete_one({"_id": oid_safe(coupon_id)})
              if is_valid_objectid(coupon_id) else None)
    flash("Coupon deleted." if result and result.deleted_count else "Coupon not found.",
          "success" if result and result.deleted_count else "danger")
    return redirect(url_for("admin_coupons"))


@app.route("/admin/reviews")
@admin_required
def admin_reviews():
    rating = request.args.get("rating")
    query = {}
    if rating in ("1", "2", "3", "4", "5"):
        query["rating"] = int(rating)
    reviews = list(reviews_collection.find(query).sort([("date", DESCENDING)]).limit(200))
    product_names = {}
    for review in reviews:
        pid = review.get("product_id")
        if pid and pid not in product_names:
            product = (products_collection.find_one({"_id": oid_safe(pid)},
                                                   {"name": 1})
                       if is_valid_objectid(pid) else None)
            product_names[pid] = product["name"] if product else "(deleted product)"
    average = list(reviews_collection.aggregate([
        {"$group": {"_id": None, "avg": {"$avg": "$rating"}, "count": {"$sum": 1}}}]))
    return render_template("admin_reviews.html", reviews=reviews, rating=rating,
                           product_names=product_names,
                           avg_rating=round(average[0]["avg"], 2) if average else 0)


@app.route("/admin/reviews/<review_id>/delete", methods=["POST"])
@admin_required
def admin_delete_review(review_id):
    result = (reviews_collection.delete_one({"_id": oid_safe(review_id)})
              if is_valid_objectid(review_id) else None)
    flash("Review deleted." if result and result.deleted_count else "Review not found.",
          "success" if result and result.deleted_count else "danger")
    return redirect(request.referrer or url_for("admin_reviews"))


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------
@app.errorhandler(400)
def bad_request(error):
    return render_template("errors/error.html", code=400, title="Bad request",
                           message="That request was missing a valid security token. "
                                   "Please go back, reload the page and try again."), 400


@app.errorhandler(403)
def forbidden(error):
    return render_template("errors/error.html", code=403, title="Access forbidden",
                           message="You do not have permission to view this page."), 403


@app.errorhandler(404)
def not_found(error):
    return render_template("errors/error.html", code=404, title="Page not found",
                           message="The page you are looking for does not exist."), 404


@app.errorhandler(PyMongoError)
@app.errorhandler(500)
def server_error(error):
    return render_template("errors/error.html", code=500, title="Something went wrong",
                           message="An unexpected error occurred. If this keeps happening, "
                                   "make sure MongoDB is running and MONGO_URI is "
                                   "correct."), 500


if __name__ == "__main__":
    app.run(debug=True)

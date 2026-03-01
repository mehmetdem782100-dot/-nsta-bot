# app.py
from flask import Flask, request, jsonify, send_file, abort
import os, json, csv, threading, uuid
from datetime import datetime

app = Flask(__name__)

DATA_DIR = "data"
PRODUCTS_FILE = os.path.join(DATA_DIR, "products.json")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
LOCK = threading.Lock()

# Admin token (set as Render environment variable ADMIN_TOKEN)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "change_me_in_render")

# Helper: ensure data dir + files exist
def ensure_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    for path, default in [
        (PRODUCTS_FILE, []),
        (ORDERS_FILE, []),
        (USERS_FILE, {})
    ]:
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f, indent=2, ensure_ascii=False)

def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

ensure_files()

# -------------------------
# Product management
# -------------------------
def list_products():
    return read_json(PRODUCTS_FILE)

def save_products(products):
    write_json(PRODUCTS_FILE, products)

def get_product_by_code(code):
    for p in list_products():
        if p.get("code") == code:
            return p
    return None

# -------------------------
# Orders
# -------------------------
def list_orders():
    return read_json(ORDERS_FILE)

def save_order(order):
    with LOCK:
        orders = list_orders()
        orders.append(order)
        write_json(ORDERS_FILE, orders)

def update_order_status(order_id, status):
    with LOCK:
        orders = list_orders()
        for o in orders:
            if o["id"] == order_id:
                o["status"] = status
                o["updated_at"] = str(datetime.now())
                write_json(ORDERS_FILE, orders)
                return o
    return None

# -------------------------
# Users (stateful chat)
# -------------------------
def get_users():
    return read_json(USERS_FILE)

def save_users(users):
    write_json(USERS_FILE, users)

# -------------------------
# Utilities
# -------------------------
def admin_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("Authorization", "")
        if token.replace("Bearer ", "") != ADMIN_TOKEN:
            return jsonify({"error":"unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

def generate_order_id():
    return uuid.uuid4().hex[:12]

# -------------------------
# Public endpoints
# -------------------------
@app.route("/")
def home():
    return "PRO BOT V2 - Aktif 🚀"

@app.route("/products", methods=["GET"])
def products():
    """Liste ürünler (public). ?q=arama için."""
    q = request.args.get("q", "").lower()
    prods = list_products()
    if q:
        prods = [p for p in prods if q in p.get("title","").lower() or q in p.get("code","").lower()]
    return jsonify(prods)

@app.route("/products/<code>", methods=["GET"])
def product_detail(code):
    p = get_product_by_code(code)
    if not p:
        return jsonify({"error":"not found"}), 404
    return jsonify(p)

# Webhook endpoint: Instagram geldiğinde burada test edebilirsin.
# Gelen örnek payload: {"user_id":"u1","message":"merhaba"}
@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.json or {}
    # Basit güvenlik: isteği logla
    print("[WEBHOOK] payload:", payload)
    user_id = str(payload.get("user_id","anonim"))
    message = str(payload.get("message","")).strip()

    users = get_users()
    if user_id not in users:
        users[user_id] = {"state":"start", "cart": [], "meta":{}}
    state = users[user_id]["state"]

    reply = "Anlayamadım. Yardım için 'menu' yaz."

    # Komutlar & akış
    msg_lower = message.lower()

    # Hızlı komutlar
    if msg_lower in ["merhaba","selam","hi","hello","menu"]:
        users[user_id]["state"] = "menu"
        reply = (
            "Merhaba 👋\n"
            "1) Ürün kataloğu için 'katalog' veya 'ürünler'\n"
            "2) Ürün detay için 'detay <kod>'\n"
            "3) Sepete ekle 'ekle <kod> <adet>'\n"
            "4) Sipariş ver 'sipariş'\n"
            "5) Siparişlerim 'siparişlerim'"
        )

    elif msg_lower.startswith("katalog") or msg_lower.startswith("ürün"):
        prods = list_products()
        lines = []
        for p in prods:
            lines.append(f"{p['code']} - {p['title']} - {p['price']} TL - Stok: {p.get('stock',0)}")
        reply = "Ürün Kataloğu:\n" + ("\n".join(lines) if lines else "Ürün yok.")

    elif msg_lower.startswith("detay"):
        parts = message.split()
        if len(parts) >= 2:
            code = parts[1].strip()
            p = get_product_by_code(code)
            if p:
                reply = f"{p['code']} - {p['title']}\nFiyat: {p['price']} TL\nStok: {p.get('stock',0)}\nAçıklama: {p.get('description','-')}"
            else:
                reply = "Ürün bulunamadı."
        else:
            reply = "Kullanım: detay <ürün_kodu>"

    elif msg_lower.startswith("ekle"):
        # ekle <kod> <adet>
        parts = message.split()
        if len(parts) >= 3:
            code = parts[1].strip()
            try:
                qty = int(parts[2])
            except:
                qty = 1
            p = get_product_by_code(code)
            if not p:
                reply = "Ürün kodu bulunamadı."
            else:
                if p.get("stock",0) < qty:
                    reply = f"Yetersiz stok. Mevcut: {p.get('stock',0)}"
                else:
                    users[user_id].setdefault("cart", [])
                    users[user_id]["cart"].append({"code":code, "qty":qty})
                    reply = f"{qty} adet {p['title']} sepete eklendi."
        else:
            reply = "Kullanım: ekle <kod> <adet>"

    elif msg_lower == "sepet":
        cart = users[user_id].get("cart",[])
        if not cart:
            reply = "Sepetiniz boş."
        else:
            lines=[]
            total=0
            for item in cart:
                p = get_product_by_code(item["code"])
                if p:
                    lines.append(f"{p['code']} {p['title']} x{item['qty']} = {p['price']*item['qty']} TL")
                    total += p['price']*item['qty']
            reply = "Sepet:\n" + "\n".join(lines) + f"\nToplam: {total} TL"

    elif msg_lower == "sipariş":
        cart = users[user_id].get("cart",[])
        if not cart:
            reply = "Sepet boş. Sepete ürün ekleyin: ekle <kod> <adet>"
        else:
            users[user_id]["state"] = "ordering_name"
            reply = "Sipariş için adınızı yazın."

    elif state == "ordering_name":
        users[user_id]["meta"]["name"] = message
        users[user_id]["state"] = "ordering_phone"
        reply = "Telefon numaranızı yazın."

    elif state == "ordering_phone":
        users[user_id]["meta"]["phone"] = message
        users[user_id]["state"] = "ordering_address"
        reply = "Adresinizi yazın."

    elif state == "ordering_address":
        users[user_id]["meta"]["address"] = message
        # Create order
        cart = users[user_id].get("cart",[])
        items=[]
        total=0
        ok = True
        # check & reserve stock
        products = list_products()
        for c in cart:
            p = get_product_by_code(c["code"])
            if not p or p.get("stock",0) < c["qty"]:
                ok = False
                break
            items.append({"code":p["code"], "title":p["title"], "qty":c["qty"], "unit_price":p["price"]})
            total += p["price"] * c["qty"]
        if not ok:
            reply = "Ürün stok hatası. Lütfen sepeti kontrol edin."
        else:
            # decrement stock
            with LOCK:
                prods = list_products()
                for p in prods:
                    for c in cart:
                        if p["code"] == c["code"]:
                            p["stock"] = p.get("stock",0) - c["qty"]
                save_products(prods)

            order = {
                "id": generate_order_id(),
                "user_id": user_id,
                "items": items,
                "total": total,
                "name": users[user_id]["meta"].get("name",""),
                "phone": users[user_id]["meta"].get("phone",""),
                "address": users[user_id]["meta"].get("address",""),
                "status": "pending",
                "created_at": str(datetime.now())
            }
            save_order(order)
            # clear cart & state
            users[user_id]["cart"] = []
            users[user_id]["state"] = "start"
            users[user_id]["meta"] = {}
            reply = f"✅ Sipariş alındı. Sipariş numaranız: {order['id']}\nToplam: {order['total']} TL\nÖdeme linki: (placeholder) https://pay.example/{order['id']}"

    elif msg_lower == "siparişlerim":
        orders = [o for o in list_orders() if o.get("user_id")==user_id]
        if not orders:
            reply = "Henüz siparişiniz yok."
        else:
            lines=[]
            for o in orders:
                lines.append(f"{o['id']} - {o['status']} - {o['total']} TL - {o['created_at']}")
            reply = "Siparişleriniz:\n" + "\n".join(lines)

    else:
        # Keyword fallback
        if "fiyat" in msg_lower:
            reply = "Fiyat soruyorsanız 'katalog' veya 'detay <kod>' kullanın."
        else:
            reply = "Komutları görmek için 'menu' yazın."

    # persist users
    save_users(users)
    return jsonify({"reply": reply})

# -------------------------
# Admin endpoints
# -------------------------
@app.route("/admin/products", methods=["GET"])
@admin_required
def admin_list_products():
    return jsonify(list_products())

@app.route("/admin/products", methods=["POST"])
@admin_required
def admin_create_product():
    body = request.json or {}
    # required fields: code,title,price,stock
    code = body.get("code")
    if not code:
        return jsonify({"error":"code required"}), 400
    prods = list_products()
    if any(p["code"]==code for p in prods):
        return jsonify({"error":"code exists"}), 400
    p = {
        "code": code,
        "title": body.get("title","Ürün"),
        "description": body.get("description",""),
        "price": int(body.get("price",0)),
        "stock": int(body.get("stock",0))
    }
    prods.append(p)
    save_products(prods)
    return jsonify(p), 201

@app.route("/admin/products/<code>", methods=["PUT","DELETE"])
@admin_required
def admin_modify_product(code):
    prods = list_products()
    idx = next((i for i,p in enumerate(prods) if p["code"]==code), None)
    if idx is None:
        return jsonify({"error":"not found"}), 404
    if request.method == "DELETE":
        prods.pop(idx)
        save_products(prods)
        return jsonify({"ok":True})
    else:
        body = request.json or {}
        prods[idx].update({
            "title": body.get("title", prods[idx]["title"]),
            "description": body.get("description", prods[idx].get("description","")),
            "price": int(body.get("price", prods[idx]["price"])),
            "stock": int(body.get("stock", prods[idx].get("stock",0)))
        })
        save_products(prods)
        return jsonify(prods[idx])

@app.route("/admin/orders", methods=["GET"])
@admin_required
def admin_list_orders():
    return jsonify(list_orders())

@app.route("/admin/orders/<order_id>", methods=["PUT"])
@admin_required
def admin_update_order(order_id):
    body = request.json or {}
    status = body.get("status")
    if not status:
        return jsonify({"error":"status required"}), 400
    updated = update_order_status(order_id, status)
    if not updated:
        return jsonify({"error":"not found"}), 404
    return jsonify(updated)

@app.route("/admin/export/orders.csv", methods=["GET"])
@admin_required
def admin_export_orders():
    orders = list_orders()
    csv_path = os.path.join(DATA_DIR, "orders_export.csv")
    with open(csv_path, "w", newline='', encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["id","user_id","name","phone","address","total","status","created_at"])
        for o in orders:
            writer.writerow([o.get(k,"") for k in ["id","user_id","name","phone","address","total","status","created_at"]])
    return send_file(csv_path, as_attachment=True)

# Health
@app.route("/health")
def health():
    return jsonify({"status":"ok", "time": str(datetime.now())})

# Admin set token helper (only for local use)
@app.route("/admin/setup-token", methods=["POST"])
def setup_token():
    # Only allow when ADMIN_TOKEN is default; not protected to keep simple for now.
    global ADMIN_TOKEN
    if ADMIN_TOKEN != "change_me_in_render":
        return jsonify({"error":"already set"}), 400
    new = request.json.get("token")
    if not new:
        return jsonify({"error":"token required"}), 400
    ADMIN_TOKEN = new
    return jsonify({"ok":True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
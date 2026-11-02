
import sqlite3
import math
import io

from datetime import datetime
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, send_file
)

app = Flask(__name__)
app.secret_key = "becora-pos-secret-key"

DATABASE = "becora.db"
LOW_STOCK_LIMIT = 5


def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_database():
    conn = get_db_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            subtotal REAL NOT NULL,
            discount REAL NOT NULL,
            total REAL NOT NULL,
            amount_paid REAL NOT NULL,
            change_due REAL NOT NULL,
            payment_method TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sale_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL,
            product_id INTEGER,
            product_name TEXT NOT NULL,
            unit_price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            line_total REAL NOT NULL,
            FOREIGN KEY (sale_id) REFERENCES sales(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
                ON DELETE SET NULL
        )
    """)

    conn.commit()
    conn.close()


# DASHBOARD
@app.route("/")
def dashboard():
    conn = get_db_connection()

    total_products = conn.execute(
        "SELECT COUNT(*) FROM products"
    ).fetchone()[0]

    total_stock = conn.execute(
        "SELECT COALESCE(SUM(stock), 0) FROM products"
    ).fetchone()[0]

    inventory_value = conn.execute(
        "SELECT COALESCE(SUM(price * stock), 0) FROM products"
    ).fetchone()[0]

    conn.close()

    return render_template(
        "dashboard.html",
        total_products=total_products,
        total_stock=total_stock,
        inventory_value=inventory_value
    )


# PRODUCTS MANAGEMENT
@app.route("/products", methods=["GET", "POST"])
def products():
    conn = get_db_connection()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()

        try:
            price = float(request.form.get("price", 0))
            stock = int(request.form.get("stock", 0))
        except (ValueError, TypeError):
            conn.close()
            flash("Please enter valid price and stock.")
            return redirect(url_for("products"))

        if (
            not name
            or not category
            or not math.isfinite(price)
            or price <= 0
            or stock < 0
        ):
            conn.close()
            flash("Please enter valid product details.")
            return redirect(url_for("products"))

        conn.execute(
            """
            INSERT INTO products (name, category, price, stock)
            VALUES (?, ?, ?, ?)
            """,
            (name, category, price, stock)
        )

        conn.commit()
        conn.close()

        return redirect(url_for("products"))

    search = request.args.get("search", "").strip()

    product_list = conn.execute(
        """
        SELECT * FROM products
        WHERE name LIKE ? OR category LIKE ?
        ORDER BY id DESC
        """,
        (f"%{search}%", f"%{search}%")
    ).fetchall()

    low_stock_count = conn.execute(
        """
        SELECT COUNT(*) FROM products
        WHERE stock <= ?
        """,
        (LOW_STOCK_LIMIT,)
    ).fetchone()[0]

    conn.close()

    return render_template(
        "products.html",
        products=product_list,
        search=search,
        low_stock_limit=LOW_STOCK_LIMIT,
        low_stock_count=low_stock_count
    )


# EDIT PRODUCT
@app.route("/products/edit/<int:product_id>", methods=["GET", "POST"])
def edit_product(product_id):
    conn = get_db_connection()

    product = conn.execute(
        "SELECT * FROM products WHERE id = ?",
        (product_id,)
    ).fetchone()

    if product is None:
        conn.close()
        return "Product not found", 404

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()

        try:
            price = float(request.form.get("price", 0))
            stock = int(request.form.get("stock", 0))
        except (ValueError, TypeError):
            conn.close()
            flash("Please enter valid price and stock.")
            return redirect(url_for("edit_product", product_id=product_id))

        if (
            not name
            or not category
            or not math.isfinite(price)
            or price <= 0
            or stock < 0
        ):
            conn.close()
            flash("Please enter valid product details.")
            return redirect(url_for("edit_product", product_id=product_id))

        conn.execute(
            """
            UPDATE products
            SET name = ?, category = ?, price = ?, stock = ?
            WHERE id = ?
            """,
            (name, category, price, stock, product_id)
        )

        conn.commit()
        conn.close()

        return redirect(url_for("products"))

    conn.close()

    return render_template(
        "edit_product.html",
        product=product
    )


# DELETE PRODUCT
@app.route("/products/delete/<int:product_id>", methods=["POST"])
def delete_product(product_id):
    conn = get_db_connection()

    conn.execute(
        "DELETE FROM products WHERE id = ?",
        (product_id,)
    )

    conn.commit()
    conn.close()

    return redirect(url_for("products"))


# POS PAGE
@app.route("/pos")
def pos():
    conn = get_db_connection()

    product_list = conn.execute(
        """
        SELECT * FROM products
        WHERE stock > 0
        ORDER BY name
        """
    ).fetchall()

    conn.close()

    return render_template(
        "pos.html",
        products=product_list
    )


# CHECKOUT
@app.route("/checkout", methods=["POST"])
def checkout():
    conn = get_db_connection()

    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return {
                "success": False,
                "message": "Invalid checkout data."
            }, 400

        cart = data.get("cart")
        amount_paid = data.get("amount_paid")
        payment_method = data.get("payment_method")

        if not isinstance(cart, list) or not cart:
            return {
                "success": False,
                "message": "Your cart is empty."
            }, 400

        if payment_method not in ("Cash", "Card"):
            raise ValueError("Please select a valid payment method.")

        if payment_method == "Cash":
            if (
                isinstance(amount_paid, bool)
                or not isinstance(amount_paid, (int, float))
                or not math.isfinite(amount_paid)
                or amount_paid < 0
            ):
                raise ValueError("Invalid cash amount.")

        quantities = {}

        for item in cart:
            if not isinstance(item, dict):
                raise ValueError("Invalid cart item.")

            product_id = item.get("id")
            quantity = item.get("quantity")

            if (
                not isinstance(product_id, int)
                or isinstance(product_id, bool)
                or not isinstance(quantity, int)
                or isinstance(quantity, bool)
                or product_id <= 0
                or quantity <= 0
            ):
                raise ValueError("Invalid product or quantity.")

            quantities[product_id] = (
                quantities.get(product_id, 0) + quantity
            )

        conn.execute("BEGIN")

        sale_items = []
        subtotal = 0

        for product_id, quantity in quantities.items():
            product = conn.execute(
                "SELECT * FROM products WHERE id = ?",
                (product_id,)
            ).fetchone()

            if product is None:
                raise ValueError("A product in your cart no longer exists.")

            if product["stock"] < quantity:
                raise ValueError(
                    f"Not enough stock for {product['name']}. "
                    f"Available: {product['stock']}."
                )

            unit_price = float(product["price"])
            line_total = round(unit_price * quantity, 2)

            subtotal += line_total

            sale_items.append({
                "product_id": product_id,
                "product_name": product["name"],
                "unit_price": unit_price,
                "quantity": quantity,
                "line_total": line_total
            })

        subtotal = round(subtotal, 2)
        discount = 0.0
        total = round(subtotal - discount, 2)

        if payment_method == "Card":
            amount_paid = total
            change_due = 0.0
        else:
            amount_paid = round(float(amount_paid), 2)

            if amount_paid < total:
                raise ValueError("Cash received is less than the total.")

            change_due = round(amount_paid - total, 2)

        cursor = conn.execute(
            """
            INSERT INTO sales
            (
                subtotal, discount, total,
                amount_paid, change_due, payment_method
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                subtotal,
                discount,
                total,
                amount_paid,
                change_due,
                payment_method
            )
        )

        sale_id = cursor.lastrowid

        for item in sale_items:
            conn.execute(
                """
                INSERT INTO sale_items
                (
                    sale_id, product_id, product_name,
                    unit_price, quantity, line_total
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sale_id,
                    item["product_id"],
                    item["product_name"],
                    item["unit_price"],
                    item["quantity"],
                    item["line_total"]
                )
            )

            conn.execute(
                """
                UPDATE products
                SET stock = stock - ?
                WHERE id = ?
                """,
                (item["quantity"], item["product_id"])
            )

        conn.commit()

        return {
            "success": True,
            "sale_id": sale_id,
            "subtotal": subtotal,
            "discount": discount,
            "total": total,
            "amount_paid": amount_paid,
            "change_due": change_due
        }

    except (ValueError, TypeError, AttributeError) as error:
        conn.rollback()

        return {
            "success": False,
            "message": str(error)
        }, 400

    except Exception:
        conn.rollback()

        return {
            "success": False,
            "message": "Checkout failed. Please try again."
        }, 500

    finally:
        conn.close()


# RECEIPT
@app.route("/receipt/<int:sale_id>")
def receipt(sale_id):
    conn = get_db_connection()

    sale = conn.execute(
        "SELECT * FROM sales WHERE id = ?",
        (sale_id,)
    ).fetchone()

    if sale is None:
        conn.close()
        return "Receipt not found", 404

    items = conn.execute(
        """
        SELECT * FROM sale_items
        WHERE sale_id = ?
        """,
        (sale_id,)
    ).fetchall()

    conn.close()

    return render_template(
        "receipt.html",
        sale=sale,
        items=items
    )


# SALES HISTORY & DAILY / MONTHLY REPORTS
@app.route("/sales-history")
def sales_history():
    conn = get_db_connection()

    sales = conn.execute(
        """
        SELECT * FROM sales
        ORDER BY id DESC
        """
    ).fetchall()

    total_sales = conn.execute(
        "SELECT COUNT(*) FROM sales"
    ).fetchone()[0]

    today_sales = conn.execute(
        """
        SELECT COUNT(*) FROM sales
        WHERE DATE(sale_date) = DATE('now', 'localtime')
        """
    ).fetchone()[0]

    month_sales = conn.execute(
        """
        SELECT COUNT(*) FROM sales
        WHERE strftime('%Y-%m', sale_date, 'localtime')
              = strftime('%Y-%m', 'now', 'localtime')
        """
    ).fetchone()[0]

    total_revenue = conn.execute(
        """
        SELECT COALESCE(SUM(total), 0) FROM sales
        """
    ).fetchone()[0]

    today_revenue = conn.execute(
        """
        SELECT COALESCE(SUM(total), 0) FROM sales
        WHERE DATE(sale_date) = DATE('now', 'localtime')
        """
    ).fetchone()[0]

    month_revenue = conn.execute(
        """
        SELECT COALESCE(SUM(total), 0) FROM sales
        WHERE strftime('%Y-%m', sale_date, 'localtime')
              = strftime('%Y-%m', 'now', 'localtime')
        """
    ).fetchone()[0]

    conn.close()

    return render_template(
        "sales_history.html",
        sales=sales,
        total_sales=total_sales,
        today_sales=today_sales,
        month_sales=month_sales,
        total_revenue=total_revenue,
        today_revenue=today_revenue,
        month_revenue=month_revenue
    )


# DATABASE BACKUP
@app.route("/backup")
def backup_database():
    source_conn = get_db_connection()
    backup_conn = sqlite3.connect(":memory:")

    try:
        source_conn.backup(backup_conn)

        backup_data = backup_conn.serialize()

        backup_file = io.BytesIO(backup_data)
        backup_file.seek(0)

        filename = (
            "BECORA_Backup_"
            + datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            + ".db"
        )

        return send_file(
            backup_file,
            mimetype="application/x-sqlite3",
            as_attachment=True,
            download_name=filename
        )

    finally:
        source_conn.close()
        backup_conn.close()


if __name__ == "__main__":
    create_database()
    app.run(debug=True)

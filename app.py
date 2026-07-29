"""
miRNA综合数据库网站 — Flask 主入口
=====================================
启动方式:  python app.py
"""

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2

from config import Config
from db import (
    search_mirna,
    count_mirna_total,
    count_tissue_samples,
    count_species,
    get_mirna_detail,
    search_by_target_gene,
    get_species_list,
    create_user_table,
    create_user,
    get_user_by_username,
    get_user_by_email,
    execute,
)
from models import User

# ── App setup ──────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config.from_object(Config)
app.config["SECRET_KEY"] = Config.SECRET_KEY

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "请先登录后再访问此页面。"
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


# ── Initialize database tables ─────────────────────────────────────────────


def init_db():
    """Create required tables if they don't exist."""
    create_user_table()


init_db()


# ── Context processor ──────────────────────────────────────────────────────


@app.context_processor
def inject_globals():
    return {"now": __import__("datetime").datetime.now()}


# ── Auth routes ────────────────────────────────────────────────────────────


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username_or_email = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Try username first, then email
        user_row = get_user_by_username(username_or_email)
        if not user_row:
            user_row = get_user_by_email(username_or_email)

        if user_row and check_password_hash(user_row["password_hash"], password):
            user = User(
                id=user_row["id"],
                username=user_row["username"],
                email=user_row["email"],
                is_active=user_row["is_active"],
            )
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))
        else:
            flash("用户名/邮箱或密码错误，请重试。", "danger")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        # Validate
        errors = []
        if not username:
            errors.append("用户名不能为空。")
        if not email or "@" not in email:
            errors.append("请输入有效的邮箱地址。")
        if len(password) < 6:
            errors.append("密码长度至少 6 位。")
        if password != confirm:
            errors.append("两次输入的密码不一致。")

        # Check uniqueness
        if get_user_by_username(username):
            errors.append("该用户名已被注册。")
        if get_user_by_email(email):
            errors.append("该邮箱已被注册。")

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            try:
                pw_hash = generate_password_hash(password)
                create_user(username, email, pw_hash)
                flash("注册成功！请登录。", "success")
                return redirect(url_for("login"))
            except Exception:
                flash("注册失败，请稍后重试。", "danger")

    return render_template("register.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("已成功退出登录。", "info")
    return redirect(url_for("login"))


# ── Main pages ─────────────────────────────────────────────────────────────


@app.route("/")
@login_required
def index():
    """Homepage with search bar and stats."""
    stats = {
        "mirna_total": count_mirna_total(),
        "tissue_samples": count_tissue_samples(),
        "species_total": count_species(),
    }
    species_list = get_species_list()
    return render_template("index.html", stats=stats, species_list=species_list)


@app.route("/search")
@login_required
def search():
    """Search results page with pagination."""
    keyword = request.args.get("q", "").strip()
    mode = request.args.get("mode", "contains")
    page = request.args.get("page", 1, type=int)
    per_page = 20

    if not keyword:
        flash("请输入搜索关键词。", "warning")
        return redirect(url_for("index"))

    results = search_mirna(keyword, mode)
    total = len(results)

    # Paginate
    start = (page - 1) * per_page
    end = start + per_page
    page_results = results[start:end]
    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "results.html",
        results=page_results,
        keyword=keyword,
        mode=mode,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@app.route("/detail/<mirna_id>")
@login_required
def detail(mirna_id):
    """miRNA detail page with tabs."""
    data = get_mirna_detail(mirna_id)
    if not data:
        flash(f"未找到 miRNA：{mirna_id}", "warning")
        return redirect(url_for("index"))
    return render_template("detail.html", data=data)


@app.route("/reverse_search", methods=["GET", "POST"])
@login_required
def reverse_search():
    """Reverse search: find miRNA by target gene."""
    results = None
    gene = ""
    if request.method == "POST":
        gene = request.form.get("gene", "").strip()
        if gene:
            results = search_by_target_gene(gene)
            if not results:
                flash(f"未找到靶向「{gene}」的 miRNA 记录。", "info")
    return render_template("reverse_search.html", results=results, gene=gene)


# ── API ────────────────────────────────────────────────────────────────────


@app.route("/api/mirna/<mirna_id>")
def api_mirna_detail(mirna_id):
    """JSON API for miRNA details."""
    data = get_mirna_detail(mirna_id)
    if not data:
        return jsonify({"error": "Not found"}), 404
    return jsonify(data)


@app.route("/api/stats")
def api_stats():
    """JSON API for homepage statistics."""
    return jsonify({
        "mirna_total": count_mirna_total(),
        "tissue_samples": count_tissue_samples(),
        "species_total": count_species(),
    })


# ── Error handlers ─────────────────────────────────────────────────────────


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 5000))
    print("=" * 60)
    print("  miRNA 综合数据库网站")
    print(f"  数据库: {Config.DB_NAME}@{Config.DB_HOST}:{Config.DB_PORT}")
    print(f"  启动地址: http://0.0.0.0:{port}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)

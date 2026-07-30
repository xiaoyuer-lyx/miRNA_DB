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


# ── Jinja2 filter: 策略详情 JSON → 中文描述 ─────────────────────────


def format_strategy_details(details, mirna_id=""):
    """将 strategy_details JSON 渲染为可读的中文描述文本。"""
    if not details:
        return "无"
    if not isinstance(details, dict):
        return str(details)

    opt_type = details.get("opt_type", "")

    # --- Detargeting（去靶向策略） ---
    if "copy_number" in details:
        copy_num = details.get("copy_number", "")
        efficiency = details.get("efficiency", "")
        combination = details.get("combination")

        lines = ["📌 去靶向策略（Detargeting）"]
        lines.append("")
        lines.append(
            f"在目标 mRNA 的 3' UTR 区域插入 {copy_num} 个"
            f" 与该 miRNA 完全互补的靶点（miRT），"
        )

        if efficiency == ">95%":
            lines.append(
                "可将该 mRNA 在特定细胞中的表达量 彻底清除（降低 >95%），"
                "几乎检测不到残留表达。"
            )
        elif efficiency and "fold" in efficiency:
            lines.append(
                f"可将该 mRNA 在特定细胞中的表达量 降低约 {efficiency}"
                "（即残留表达约为原来的 1/10）。"
            )
        else:
            lines.append(f"可实现 {efficiency} 的表达抑制效果。")

        if combination:
            lines.append("")
            lines.append(f"联合策略：与 {combination} 协同作用。")

        return "\n".join(lines)

    # --- Co_Delivery（共递送策略） ---
    if "delivery_tech" in details and "inhibitor_type" in details:
        tech = details["delivery_tech"]
        inhibitor = details["inhibitor_type"]

        lines = ["📌 共递送策略（Co-Delivery）"]
        lines.append("")

        # 根据 delivery_tech 翻译
        tech_cn_map = {
            "Trimannose conjugation": "三甘露糖（Trimannose）偶联技术",
            "LNP co-delivery": "脂质纳米颗粒（LNP）共递送技术",
        }
        tech_cn = tech_cn_map.get(tech, tech)

        # 根据 inhibitor_type 翻译
        inhibitor_desc = (
            f"{inhibitor}（一种与 miRNA 完全互补的人工合成 RNA 抑制剂）"
        )

        lines.append(
            f"将 {inhibitor_desc} 通过 {tech_cn} 进行化学修饰，"
        )
        lines.append(
            "利用靶细胞表面特异性识别该修饰的受体，"
            "实现 精准靶向递送至目标细胞，"
            "从而在细胞内中和 miRNA 的活性，减轻相关病理反应。"
        )

        return "\n".join(lines)

    # --- Codon_Escape（密码子伪装策略） ---
    if "wild_codon" in details and "mutant_codon" in details:
        wt = details["wild_codon"]
        mt = details["mutant_codon"]
        aa = details.get("amino_acid", "")

        lines = ["📌 密码子伪装策略（Codon Escape）"]
        lines.append("")
        lines.append(
            f"在 mRNA 编码区中，将原始的 危险密码子 {wt}"
            f"（编码 {aa}）"
        )
        lines.append(
            f"通过同义突变改为 {mt}（仍编码 {aa}），"
        )
        lines.append(
            "在不改变蛋白质氨基酸序列的前提下，"
            "破坏 miRNA 种子区与 mRNA 的结合位点，"
            "使该 mRNA 能够逃逸 miRNA 介导的翻译抑制。"
        )

        return "\n".join(lines)

    # --- 兜底：格式化 JSON key-value ---
    lines = ["📌 策略详情"]
    for k, v in details.items():
        lines.append(f"  • {k}: {v}")
    return "\n".join(lines)


app.jinja_env.filters["fmt_strategy"] = format_strategy_details


@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


# ── Initialize database tables ─────────────────────────────────────────────


def init_db():
    """Create required tables if they don't exist."""
    create_user_table()
    migrate_add_display_columns()


def migrate_add_display_columns():
    """Add mirbase_id and mirgenedb_id columns if missing (for remote DB)."""
    try:
        execute("""
            ALTER TABLE mirna_core_info
            ADD COLUMN IF NOT EXISTS mirbase_id VARCHAR(50);
        """)
        execute("""
            ALTER TABLE mirna_core_info
            ADD COLUMN IF NOT EXISTS mirgenedb_id VARCHAR(50);
        """)
        execute("""
            ALTER TABLE mirna_expression_bias
            ADD COLUMN IF NOT EXISTS mirbase_id VARCHAR(50);
        """)
        execute("""
            ALTER TABLE mirna_expression_bias
            ADD COLUMN IF NOT EXISTS mirgenedb_id VARCHAR(50);
        """)
        print("  ✅ 显示列迁移完成")

        # Fill mirbase_id for Drosophila using MirGeneDB→miRBase mapping
        # Only run if mirbase_id is still NULL for Drosophila rows
        DME_MAPPING = {
            "Dme-Bantam_pri": "dme-bantam",
            "Dme-Iab-4-as_pri": "dme-mir-iab-8",
            "Dme-Iab-4_pri": "dme-mir-iab-4",
            "Dme-Let-7_pri": "dme-let-7",
            "Dme-Mir-10-P1_pri": "dme-mir-10",
            "Dme-Mir-10-P2_pri": "dme-mir-100",
            "Dme-Mir-10-P3_pri": "dme-mir-125",
            "Dme-Mir-10-P4_pri": "dme-mir-993",
            "Dme-Mir-1000_pri": "dme-mir-1000",
            "Dme-Mir-1001_pri": "dme-mir-1001",
            "Dme-Mir-1003_pri": "dme-mir-1003",
            "Dme-Mir-1006_pri": "dme-mir-1006",
            "Dme-Mir-1007_pri": "dme-mir-1007",
            "Dme-Mir-1010_pri": "dme-mir-1010",
            "Dme-Mir-1175_pri": "dme-mir-958",
            "Dme-Mir-124_pri": "dme-mir-124",
            "Dme-Mir-12_pri": "dme-mir-12",
            "Dme-Mir-133_pri": "dme-mir-133",
            "Dme-Mir-137_pri": "dme-mir-137",
            "Dme-Mir-14_pri": "dme-mir-14",
            "Dme-Mir-184_pri": "dme-mir-184",
            "Dme-Mir-190_pri": "dme-mir-190",
            "Dme-Mir-193-P1-v1_pri": "dme-mir-193",
            "Dme-Mir-193-P1-v2_pri": "dme-mir-193",
            "Dme-Mir-193-P1-v3_pri": "dme-mir-193",
            "Dme-Mir-193-P1-v4_pri": "dme-mir-193",
            "Dme-Mir-1_pri": "dme-mir-1",
            "Dme-Mir-2-P1-v1_pri": "dme-mir-2c",
            "Dme-Mir-2-P1-v2_pri": "dme-mir-2c",
            "Dme-Mir-2-P2a_pri": "dme-mir-13a",
            "Dme-Mir-2-P2b1_pri": "dme-mir-13b-1",
            "Dme-Mir-2-P2b2_pri": "dme-mir-13b-2",
            "Dme-Mir-2-P3a_pri": "dme-mir-2b-1",
            "Dme-Mir-2-P3b_pri": "dme-mir-2b-2",
            "Dme-Mir-2-P4a-v1_pri": "dme-mir-2a-1",
            "Dme-Mir-2-P4a-v2_pri": "dme-mir-2a-1",
            "Dme-Mir-2-P4b-v1_pri": "dme-mir-2a-2",
            "Dme-Mir-2-P4b-v2_pri": "dme-mir-2a-2",
            "Dme-Mir-2-P5_pri": "dme-mir-5",
            "Dme-Mir-2-P6a_pri": "dme-mir-6-1",
            "Dme-Mir-2-P6b_pri": "dme-mir-6-2",
            "Dme-Mir-2-P6c_pri": "dme-mir-6-3",
            "Dme-Mir-2-P7_pri": "dme-mir-11",
            "Dme-Mir-2-P8-v1_pri": "dme-mir-308",
            "Dme-Mir-2-P8-v2_pri": "dme-mir-308",
            "Dme-Mir-2-P9-v1_pri": "dme-mir-994",
            "Dme-Mir-2-P9-v2_pri": "dme-mir-994",
            "Dme-Mir-2001_pri": "dme-mir-274",
            "Dme-Mir-210-v1_pri": "dme-mir-210",
            "Dme-Mir-210-v2_pri": "dme-mir-210",
            "Dme-Mir-216-P1_pri": "dme-mir-283",
            "Dme-Mir-216-P2_pri": "dme-mir-304",
            "Dme-Mir-219_pri": "dme-mir-219",
            "Dme-Mir-22-P2_pri": "dme-mir-980",
            "Dme-Mir-2279_pri": "dme-mir-2279",
            "Dme-Mir-2499_pri": "dme-mir-2499",
            "Dme-Mir-252-P1a_pri": "dme-mir-968",
            "Dme-Mir-252-P2-v1_pri": "dme-mir-252",
            "Dme-Mir-252-P2-v2_pri": "dme-mir-252",
            "Dme-Mir-275_pri": "dme-mir-275",
            "Dme-Mir-276-P1_pri": "dme-mir-276b",
            "Dme-Mir-276-P2_pri": "dme-mir-276a",
            "Dme-Mir-277_pri": "dme-mir-277",
            "Dme-Mir-278_pri": "dme-mir-278",
            "Dme-Mir-279-P1_pri": "dme-mir-286",
            "Dme-Mir-279-P2_pri": "dme-mir-279",
            "Dme-Mir-279-P3_pri": "dme-mir-996",
            "Dme-Mir-281-P1-v1_pri": "dme-mir-281-1",
            "Dme-Mir-281-P1-v2_pri": "dme-mir-281-1",
            "Dme-Mir-281-P2-v1_pri": "dme-mir-281-2",
            "Dme-Mir-281-P2-v2_pri": "dme-mir-281-2",
            "Dme-Mir-282_pri": "dme-mir-282",
            "Dme-Mir-284_pri": "dme-mir-284",
            "Dme-Mir-29-P1_pri": "dme-mir-285",
            "Dme-Mir-29-P2g_pri": "dme-mir-995",
            "Dme-Mir-29-P2h_pri": "dme-mir-998",
            "Dme-Mir-3-P1_pri": "dme-mir-309",
            "Dme-Mir-3-P2_pri": "dme-mir-3",
            "Dme-Mir-3-P3_pri": "dme-mir-318",
            "Dme-Mir-303_pri": "dme-mir-303",
            "Dme-Mir-305_pri": "dme-mir-305",
            "Dme-Mir-306_pri": "dme-mir-306",
            "Dme-Mir-31-P1_pri": "dme-mir-31a",
            "Dme-Mir-31-P2_pri": "dme-mir-31b",
            "Dme-Mir-314_pri": "dme-mir-314",
            "Dme-Mir-315_pri": "dme-mir-315",
            "Dme-Mir-316_pri": "dme-mir-316",
            "Dme-Mir-317_pri": "dme-mir-317",
            "Dme-Mir-33_pri": "dme-mir-33",
            "Dme-Mir-34_pri": "dme-mir-34",
            "Dme-Mir-375_pri": "dme-mir-375",
            "Dme-Mir-4969_pri": "dme-mir-4969",
            "Dme-Mir-4983_pri": "dme-mir-4983",
            "Dme-Mir-67-v1_pri": "dme-mir-307a",
            "Dme-Mir-67-v2_pri": "dme-mir-307a",
            "Dme-Mir-67-v3_pri": "dme-mir-307a",
            "Dme-Mir-76_pri": "dme-mir-981",
            "Dme-Mir-7_pri": "dme-mir-7",
            "Dme-Mir-87-P2-v1_pri": "dme-mir-87",
            "Dme-Mir-87-P2-v2_pri": "dme-mir-87",
            "Dme-Mir-8_pri": "dme-mir-8",
            "Dme-Mir-9-P10_pri": "dme-mir-9a",
            "Dme-Mir-9-P11_pri": "dme-mir-9c",
            "Dme-Mir-9-P12-v1_pri": "dme-mir-79",
            "Dme-Mir-9-P12-v2_pri": "dme-mir-79",
            "Dme-Mir-9-P13_pri": "dme-mir-9b",
            "Dme-Mir-9-P9_pri": "dme-mir-4",
            "Dme-Mir-92-P3_pri": "dme-mir-92a",
            "Dme-Mir-92-P4_pri": "dme-mir-92b",
            "Dme-Mir-92-P5_pri": "dme-mir-310",
            "Dme-Mir-92-P6_pri": "dme-mir-311",
            "Dme-Mir-92-P7_pri": "dme-mir-312",
            "Dme-Mir-92-P8_pri": "dme-mir-313",
            "Dme-Mir-927-P1_pri": "dme-mir-927",
            "Dme-Mir-929_pri": "dme-mir-929",
            "Dme-Mir-932_pri": "dme-mir-932",
            "Dme-Mir-9388_pri": "dme-mir-9388",
            "Dme-Mir-955_pri": "dme-mir-955",
            "Dme-Mir-956-v1_pri": "dme-mir-956",
            "Dme-Mir-956-v2_pri": "dme-mir-956",
            "Dme-Mir-957_pri": "dme-mir-957",
            "Dme-Mir-959-v1_pri": "dme-mir-959",
            "Dme-Mir-959-v2_pri": "dme-mir-959",
            "Dme-Mir-96-P2_pri": "dme-mir-263b",
            "Dme-Mir-96-P3_pri": "dme-mir-263a",
            "Dme-Mir-960_pri": "dme-mir-960",
            "Dme-Mir-961_pri": "dme-mir-961",
            "Dme-Mir-962_pri": "dme-mir-962",
            "Dme-Mir-963-v1_pri": "dme-mir-963",
            "Dme-Mir-963-v2_pri": "dme-mir-963",
            "Dme-Mir-964-v1_pri": "dme-mir-964",
            "Dme-Mir-964-v2_pri": "dme-mir-964",
            "Dme-Mir-965_pri": "dme-mir-965",
            "Dme-Mir-966_pri": "dme-mir-966",
            "Dme-Mir-967_pri": "dme-mir-967",
            "Dme-Mir-969_pri": "dme-mir-969",
            "Dme-Mir-970_pri": "dme-mir-970",
            "Dme-Mir-971_pri": "dme-mir-971",
            "Dme-Mir-972_pri": "dme-mir-972",
            "Dme-Mir-973_pri": "dme-mir-973",
            "Dme-Mir-974_pri": "dme-mir-974",
            "Dme-Mir-975_pri": "dme-mir-975",
            "Dme-Mir-976_pri": "dme-mir-976",
            "Dme-Mir-977_pri": "dme-mir-977",
            "Dme-Mir-978-v1_pri": "dme-mir-978",
            "Dme-Mir-978-v2_pri": "dme-mir-978",
            "Dme-Mir-982_pri": "dme-mir-982",
            "Dme-Mir-983-P1_pri": "dme-mir-983-2",
            "Dme-Mir-983-P2_pri": "dme-mir-983-1",
            "Dme-Mir-984_pri": "dme-mir-984",
            "Dme-Mir-985_pri": "dme-mir-985",
            "Dme-Mir-986_pri": "dme-mir-986",
            "Dme-Mir-987_pri": "dme-mir-987",
            "Dme-Mir-988_pri": "dme-mir-988",
            "Dme-Mir-989_pri": "dme-mir-989",
            "Dme-Mir-991_pri": "dme-mir-991",
            "Dme-Mir-992_pri": "dme-mir-992",
            "Dme-Mir-997_pri": "dme-mir-997",
            "Dme-Mir-999_pri": "dme-mir-999",
        }
        import psycopg2
        conn = psycopg2.connect(Config.get_dsn())
        cur = conn.cursor()
        cur.execute(
            "SELECT mirna_id FROM mirna_core_info WHERE species = 'Drosophila melanogaster' AND mirbase_id IS NULL"
        )
        for row in cur.fetchall():
            mid = row[0]
            mb = DME_MAPPING.get(mid)
            if mb:
                cur.execute("UPDATE mirna_core_info SET mirbase_id = %s, mirgenedb_id = %s WHERE mirna_id = %s",
                            (mb, mid, mid))
            else:
                cur.execute("UPDATE mirna_core_info SET mirbase_id = mirna_id, mirgenedb_id = mirna_id WHERE mirna_id = %s",
                            (mid,))
        cur.execute(
            "SELECT id, mirna_id FROM mirna_expression_bias WHERE species = 'Drosophila melanogaster' AND mirbase_id IS NULL"
        )
        for row in cur.fetchall():
            rid, mid = row
            mb = DME_MAPPING.get(mid)
            if mb:
                cur.execute("UPDATE mirna_expression_bias SET mirbase_id = %s, mirgenedb_id = %s WHERE id = %s",
                            (mb, mid, rid))
            else:
                cur.execute("UPDATE mirna_expression_bias SET mirbase_id = mirna_id, mirgenedb_id = mirna_id WHERE id = %s",
                            (rid,))
        conn.commit()
        cur.close()
        conn.close()
        print("  ✅ Drosophila mirbase_id 映射完成")
    except Exception as e:
        print(f"  ⚠ 迁移跳过: {e}")


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
    """Search results page with pagination.
    空关键词 = 浏览全部，不报错。
    支持 species 参数限定物种。
    """
    keyword = request.args.get("q", "").strip()
    mode = request.args.get("mode", "contains")
    species = request.args.get("species", "all")
    page = request.args.get("page", 1, type=int)
    per_page = 20

    if not keyword:
        results = search_mirna("", mode, species)
    else:
        results = search_mirna(keyword, mode, species)
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
        species=species,
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

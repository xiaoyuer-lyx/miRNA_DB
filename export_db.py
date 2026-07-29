"""
数据库导出脚本
从本地 PostgreSQL 导出所有 miRNA 数据为 SQL 文件，
以便导入 Render PostgreSQL。
"""

import psycopg2
import psycopg2.extras
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")

# ── 本地数据库配置 ─────────────────────────────────────────────────────
LOCAL_CONFIG = {
    "host": os.getenv("LOCAL_DB_HOST", "localhost"),
    "port": int(os.getenv("LOCAL_DB_PORT", "5432")),
    "dbname": os.getenv("LOCAL_DB_NAME", "mirna_db"),
    "user": os.getenv("LOCAL_DB_USER", "postgres"),
    "password": os.getenv("LOCAL_DB_PASSWORD", ""),
}

OUTPUT_FILE = os.getenv("OUTPUT_FILE", "mirna_db_export.sql")

# ── 要导出的表（按依赖顺序，先导主表再导子表） ───────────────────
TABLES = [
    "mirna_core_info",
    "mirna_expression_bias",
    "mirna_functional_bias",
    "mirna_engineering_optimization",
    "mirna_target_interactions",
]


def get_columns(cur, table):
    """获取表的列名列表，跳过 SERIAL 自增字段 (id)。"""
    cur.execute(
        """
        SELECT column_name, column_default, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """,
        (table,),
    )
    cols = []
    for row in cur.fetchall():
        col_name = row["column_name"]
        col_default = row["column_default"]
        # 跳过 SERIAL 自增字段（默认值含 nextval）
        if col_default and "nextval" in col_default:
            continue
        cols.append(col_name)
    return cols


def escape_value(val):
    """将 Python 值转义为 SQL 字面量。"""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return str(val)
    # 字符串转义
    escaped = str(val).replace("'", "''").replace("\\", "\\\\")
    return f"'{escaped}'"


def export_table(cur, table):
    """导出整张表为 INSERT 语句。"""
    cols = get_columns(cur, table)
    # 查询时带列名，ORDER BY 第一列
    first_col = cols[0] if cols else "1"
    col_identifiers = ", ".join(f'"{c}"' for c in cols)

    cur.execute(f'SELECT {col_identifiers} FROM "{table}" ORDER BY "{first_col}"')
    rows = cur.fetchall()

    if not rows:
        print(f"  {table}: 0 条记录，跳过")
        return ""

    sql_parts = []
    sql_parts.append(f"\n-- ========================================")
    sql_parts.append(f"-- Table: {table} ({len(rows)} rows)")
    sql_parts.append(f"-- ========================================")
    sql_parts.append(f'TRUNCATE TABLE "{table}" CASCADE;')

    # 按每批 500 条生成批量 INSERT
    BATCH = 500
    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
        values_list = []
        for row in batch:
            escaped = [escape_value(v) for v in row]
            values_list.append(f"({', '.join(escaped)})")
        sql_parts.append(
            f'INSERT INTO "{table}" ({col_identifiers}) VALUES\n'
            + ",\n".join(values_list)
            + ";"
        )

    print(f"  {table}: {len(rows)} 条记录已导出")
    return "\n".join(sql_parts)


def export_users_table(cur):
    """导出 users 表结构（不含数据）。"""
    return """
-- ========================================
-- Table: users (结构仅建表，不含用户数据)
-- ========================================
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);
"""


def main():
    print("=" * 60)
    print("  miRNA 数据库导出工具")
    print(f"  本地数据库: {LOCAL_CONFIG['dbname']}@{LOCAL_CONFIG['host']}")
    print(f"  输出文件: {OUTPUT_FILE}")
    print("=" * 60)

    # 连接本地数据库
    dsn = (
        f"host={LOCAL_CONFIG['host']} port={LOCAL_CONFIG['port']} "
        f"dbname={LOCAL_CONFIG['dbname']} user={LOCAL_CONFIG['user']} "
        f"password={LOCAL_CONFIG['password']}"
    )
    print(f"\n🔗 正在连接本地数据库...")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    print("  ✅ 连接成功\n")

    # 生成 SQL
    all_sql = []
    all_sql.append("-- ============================================")
    all_sql.append("-- miRNA 综合数据库 — 数据导出")
    all_sql.append(f"-- 导出时间: {__import__('datetime').datetime.now()}")
    all_sql.append("-- ============================================")
    all_sql.append("")
    all_sql.append("BEGIN;")
    all_sql.append("")

    # 1. 建表（users + 数据表的主表必须先存在）
    all_sql.append(export_users_table(cur))

    # 2. 导出数据
    for table in TABLES:
        sql = export_table(cur, table)
        if sql:
            all_sql.append(sql)

    all_sql.append("")
    all_sql.append("COMMIT;")

    # 写入文件
    output = "\n".join(all_sql)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(output)

    file_size = os.path.getsize(OUTPUT_FILE)
    print(f"\n📦 导出完成!")
    print(f"   文件: {OUTPUT_FILE}")
    print(f"   大小: {file_size / 1024 / 1024:.2f} MB")

    cur.close()
    conn.close()
    print("\n✨ 完成！")


if __name__ == "__main__":
    main()

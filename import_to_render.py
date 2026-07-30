"""
数据迁移脚本：从本地 PostgreSQL 直接传输到远程 Render PostgreSQL
含：自动同步表结构 + 逐表逐批传输数据
"""

import os
import sys
import psycopg2
import psycopg2.extras
import json

# 注册 JSON 适配器
psycopg2.extras.register_default_json(loads=json.loads)
psycopg2.extras.register_default_jsonb(loads=json.loads)
psycopg2.extensions.register_adapter(dict, psycopg2.extras.Json)

sys.stdout.reconfigure(encoding="utf-8")

# ── 配置 ────────────────────────────────────────────────────────────
REMOTE_URL = os.getenv("REMOTE_DATABASE_URL", "").strip()
LOCAL_PASSWORD = os.getenv("LOCAL_DB_PASSWORD", "788104")

if not REMOTE_URL:
    print("错误：请先设置 REMOTE_DATABASE_URL")
    sys.exit(1)

LOCAL_DSN = f"host=localhost port=5432 dbname=mirna_db user=postgres password={LOCAL_PASSWORD}"
REMOTE_DSN = REMOTE_URL.replace("postgresql://", "postgres://", 1)

BATCH_SIZE = 500

# ── 表定义 ──────────────────────────────────────────────────────────
TABLES = [
    {
        "name": "mirna_core_info",
        "columns": ["mirna_id", "species", "mature_sequence", "seed_sequence", "is_dicer_dependent"],
    },
    {
        "name": "mirna_expression_bias",
        "columns": ["mirna_id", "species", "context_type", "context_name",
                     "expression_value", "expression_unit", "tissue_specificity_index", "data_source"],
    },
    {
        "name": "mirna_functional_bias",
        "columns": ["mirna_id", "bias_category", "bias_name", "bias_description",
                     "context_warning", "data_source"],
    },
    {
        "name": "mirna_engineering_optimization",
        "columns": ["mirna_id", "opt_type", "target_cell_type", "application_scenario",
                     "strategy_details", "data_source"],
    },
    {
        "name": "mirna_target_interactions",
        "columns": ["mirna_id", "target_gene", "interaction_type", "binding_affinity",
                     "experimental_evidence", "data_source"],
    },
]


def get_local_connection():
    return psycopg2.connect(LOCAL_DSN)


def get_remote_connection():
    return psycopg2.connect(REMOTE_DSN, connect_timeout=30, sslmode="require")


def get_create_table_sql(local_conn, table_name):
    """从本地数据库获取建表 DDL"""
    cur = local_conn.cursor()

    # 获取列定义
    cur.execute("""
        SELECT column_name, data_type, character_maximum_length,
               column_default, is_nullable, udt_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, (table_name,))

    columns = cur.fetchall()

    # 获取主键
    cur.execute("""
        SELECT c.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
        JOIN information_schema.columns c ON c.table_name = tc.table_name AND c.column_name = ccu.column_name
        WHERE tc.table_schema = 'public' AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
    """, (table_name,))
    pk_cols = [r[0] for r in cur.fetchall()]

    # 获取外键
    cur.execute("""
        SELECT
            kcu.column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
        WHERE tc.table_schema = 'public' AND tc.table_name = %s
          AND tc.constraint_type = 'FOREIGN KEY'
    """, (table_name,))
    fk_cols = cur.fetchall()

    col_defs = []
    for col in columns:
        col_name, data_type, char_max, default, nullable, udt_name = col

        # 类型映射
        if data_type == "USER-DEFINED":
            pg_type = udt_name
        elif data_type == "character varying":
            pg_type = f"varchar({char_max})" if char_max else "varchar"
        elif data_type == "character":
            pg_type = f"char({char_max})" if char_max else "char"
        elif data_type == "timestamp without time zone":
            pg_type = "timestamp"
        elif data_type == "double precision":
            pg_type = "double precision"
        elif data_type == "boolean":
            pg_type = "boolean"
        elif data_type == "real":
            pg_type = "real"
        elif data_type == "jsonb":
            pg_type = "jsonb"
        elif data_type == "integer":
            # 检查是否为 SERIAL
            if default and "nextval" in default:
                pg_type = "SERIAL"
                default = None
            else:
                pg_type = "integer"
        else:
            pg_type = data_type

        col_def = f"    {col_name} {pg_type}"
        if nullable == "NO" and col_name not in pk_cols and pg_type != "SERIAL":
            col_def += " NOT NULL"
        if default and "nextval" not in default:
            col_def += f" DEFAULT {default}"
        col_defs.append(col_def)

    # 主键
    if pk_cols:
        col_defs.append(f"    PRIMARY KEY ({', '.join(pk_cols)})")

    # 外键
    for fk in fk_cols:
        col_defs.append(f"    FOREIGN KEY ({fk[0]}) REFERENCES {fk[1]}({fk[2]})")

    sql = f"CREATE TABLE IF NOT EXISTS {table_name} (\n" + ",\n".join(col_defs) + "\n);"
    cur.close()
    return sql


def stream_insert(local_conn, remote_conn, table_def):
    """数据逐批传输"""
    table_name = table_def["name"]
    columns = table_def["columns"]
    col_names = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))

    local_cur = local_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    local_cur.execute(f"SELECT {col_names} FROM {table_name} ORDER BY {columns[0]}")

    remote_cur = remote_conn.cursor()
    remote_cur.execute(f"TRUNCATE TABLE {table_name} CASCADE;")
    remote_conn.commit()

    batch = []
    total = 0
    batch_count = 0

    for row in local_cur:
        batch.append(tuple(row[c] for c in columns))
        total += 1
        if len(batch) >= BATCH_SIZE:
            psycopg2.extras.execute_values(
                remote_cur,
                f"INSERT INTO {table_name} ({col_names}) VALUES %s",
                batch,
                template=f"({placeholders})",
            )
            remote_conn.commit()
            batch_count += len(batch)
            print(f"  ⏳ {table_name}: {batch_count} 条...", end="\r")
            batch = []

    if batch:
        psycopg2.extras.execute_values(
            remote_cur,
            f"INSERT INTO {table_name} ({col_names}) VALUES %s",
            batch,
            template=f"({placeholders})",
        )
        remote_conn.commit()
        batch_count += len(batch)

    local_cur.close()
    remote_cur.close()
    return total


def main():
    print("=" * 60)
    print("  miRNA 数据库迁移工具")
    print("  本地 → Render PostgreSQL")
    print("=" * 60)

    # 测试连接
    print("\n🔗 测试本地连接...")
    try:
        local_conn = get_local_connection()
        print("  ✅ 本地连接成功")
    except Exception as e:
        print(f"  ❌ 本地连接失败: {e}")
        sys.exit(1)

    print("🔗 测试远程连接...")
    try:
        remote_conn = get_remote_connection()
        print("  ✅ 远程连接成功")
    except Exception as e:
        print(f"  ❌ 远程连接失败: {e}")
        sys.exit(1)

    # 步骤1：同步表结构到远程
    print("\n📦 步骤 1/3: 同步表结构...")

    # 1a. users 表
    remote_cur = remote_conn.cursor()
    remote_cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        );
    """)
    remote_conn.commit()
    remote_cur.close()
    print("  ✅ users 表已就绪")

    # 1b. 数据表
    for table_def in TABLES:
        try:
            ddl_sql = get_create_table_sql(local_conn, table_def["name"])
            remote_cur = remote_conn.cursor()
            remote_cur.execute(ddl_sql)
            remote_conn.commit()
            remote_cur.close()
            print(f"  ✅ {table_def['name']} 表已创建")
        except Exception as e:
            print(f"  ⚠ {table_def['name']}: {e}")
            remote_conn.rollback()

    # 步骤2：迁移数据
    print("\n📤 步骤 2/3: 迁移数据...\n")
    for table_def in TABLES:
        try:
            n = stream_insert(local_conn, remote_conn, table_def)
            print(f"  ✅ {table_def['name']}: {n} 条记录迁移完成" + " " * 20)
        except Exception as e:
            print(f"  ❌ {table_def['name']} 失败: {e}")
            try:
                remote_conn.close()
            except:
                pass
            remote_conn = get_remote_connection()

    # 步骤3：验证
    print("\n📊 步骤 3/3: 验证远程数据库...")
    cur = remote_conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
    tables = cur.fetchall()
    for t in tables:
        cur.execute(f"SELECT COUNT(*) FROM {t[0]}")
        cnt = cur.fetchone()[0]
        print(f"  - {t[0]}: {cnt} 条记录")
    cur.close()

    local_conn.close()
    remote_conn.close()
    print(f"\n✨ 迁移完成！")


if __name__ == "__main__":
    main()

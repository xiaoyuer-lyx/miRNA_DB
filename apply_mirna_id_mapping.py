"""
将 Drosophila 的 mirna_id 映射关系写入数据库
在 core_info 和 expression_bias 中添加:
  - mirgenedb_id: MirGeneDB 原始 ID（当前 mirna_id 的原始值）
  - mirbase_id:   miRBase 标准 ID（用于网页显示）

注意：多个 MirGeneDB ID 可能映射到同一 miRBase ID，
因此 mirna_id 保持为 MirGeneDB ID（唯一），mirbase_id 用于显示。
"""

import psycopg2
import sys

sys.stdout.reconfigure(encoding="utf-8")

DB_DSN = "host=localhost port=5432 dbname=mirna_db user=postgres password=788104"
MAPPING_SQL = r"C:\Users\liyuxuan\PycharmProjects\PythonProject\华大基因实习\miRBase与MirGeneDB映射表\insert_dme_melanogaster_mirgenedb_expression_bias.sql"

# ── 从映射 SQL 中提取映射对 ──────────────────────────────────────────
print("📖 读取映射文件...")
# 用 set 去重
pairs = set()
with open(MAPPING_SQL, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line.startswith("("):
            parts = line.strip("(),;").split(",")
            if len(parts) >= 2:
                mirbase = parts[0].strip().strip("'")
                mirgenedb = parts[1].strip().strip("'")
                if mirbase.startswith("dme-") and mirgenedb.startswith("Dme-"):
                    pairs.add((mirbase, mirgenedb))

# MirGeneDB → miRBase 查找
mirgenedb_to_mirbase = {}
for mirbase, mirgenedb in pairs:
    if mirgenedb not in mirgenedb_to_mirbase:
        mirgenedb_to_mirbase[mirgenedb] = mirbase

print(f"  提取到 {len(pairs)} 个映射对，{len(mirgenedb_to_mirbase)} 个唯一 MirGeneDB ID")

# ── 连接数据库 ────────────────────────────────────────────────────────
conn = psycopg2.connect(DB_DSN)
cur = conn.cursor()

# 1. 给 mirna_core_info 添加 mirbase_id 和 mirgenedb_id 列
print("\n📦 步骤 1: 给 mirna_core_info 添加 mirbase_id, mirgenedb_id...")
cur.execute("""
    ALTER TABLE mirna_core_info
    ADD COLUMN IF NOT EXISTS mirbase_id VARCHAR(50);
""")
cur.execute("""
    ALTER TABLE mirna_core_info
    ADD COLUMN IF NOT EXISTS mirgenedb_id VARCHAR(50);
""")
conn.commit()
print("  ✅ 列添加成功")

# 2. 填充 mirgenedb_id（备份当前 mirna_id）
#    已经做过，但确保所有都填充
print("\n📦 步骤 2: 填充 mirgenedb_id（若为空则等于 mirna_id）...")
cur.execute("""
    UPDATE mirna_core_info
    SET mirgenedb_id = mirna_id
    WHERE species = 'Drosophila melanogaster' AND mirgenedb_id IS NULL
""")
print(f"  ✅ 填充 {cur.rowcount} 条")
conn.commit()

# 3. 填充 mirbase_id
print("\n📦 步骤 3: 填写 mirbase_id（miRBase 标准 ID）...")
cur.execute("""
    SELECT mirna_id FROM mirna_core_info
    WHERE species = 'Drosophila melanogaster'
""")
updated = 0
not_found = []
for row in cur.fetchall():
    mid = row[0]
    if mid in mirgenedb_to_mirbase:
        cur.execute(
            "UPDATE mirna_core_info SET mirbase_id = %s WHERE mirna_id = %s",
            (mirgenedb_to_mirbase[mid], mid),
        )
        updated += 1
    else:
        # 就用原来 ID 的小写版本
        not_found.append(mid)
conn.commit()
print(f"  ✅ 已填写 {updated} 条")
if not_found:
    print(f"  ⚠ 映射表中未找到 {len(not_found)} 条，用原 ID: {not_found[:5]}...")

# 4. 给 mirna_expression_bias 也加列
print("\n📦 步骤 4: 给 mirna_expression_bias 添加 mirbase_id, mirgenedb_id...")
cur.execute("""
    ALTER TABLE mirna_expression_bias
    ADD COLUMN IF NOT EXISTS mirbase_id VARCHAR(50);
""")
cur.execute("""
    ALTER TABLE mirna_expression_bias
    ADD COLUMN IF NOT EXISTS mirgenedb_id VARCHAR(50);
""")
conn.commit()
print("  ✅ 列添加成功")

# 5. 填充 expression_bias 的 mirgenedb_id 和 mirbase_id
print("\n📦 步骤 5: 填充 mirna_expression_bias...")
cur.execute("""
    SELECT id, mirna_id FROM mirna_expression_bias
    WHERE species = 'Drosophila melanogaster'
""")
expr_updated = 0
for rid, mid in cur.fetchall():
    if mid in mirgenedb_to_mirbase:
        mirbase = mirgenedb_to_mirbase[mid]
        cur.execute(
            "UPDATE mirna_expression_bias SET mirgenedb_id = %s, mirbase_id = %s WHERE id = %s",
            (mid, mirbase, rid),
        )
        expr_updated += 1
conn.commit()
print(f"  ✅ 已填充 {expr_updated} 条")

# 6. 验证
print("\n📊 验证...")
cur.execute("SELECT COUNT(*) FROM mirna_core_info WHERE species = 'Drosophila melanogaster'")
total = cur.fetchone()[0]
cur.execute("""
    SELECT COUNT(*) FROM mirna_core_info
    WHERE species = 'Drosophila melanogaster' AND mirbase_id IS NOT NULL
""")
has_mirbase = cur.fetchone()[0]
cur.execute("""
    SELECT COUNT(*) FROM mirna_core_info
    WHERE species = 'Drosophila melanogaster' AND mirgenedb_id IS NOT NULL
""")
has_mirgenedb = cur.fetchone()[0]
print(f"  Drosophila 总数: {total}")
print(f"  有 mirbase_id: {has_mirbase}")
print(f"  有 mirgenedb_id: {has_mirgenedb}")

# 验证 JOIN（应该没问题）
cur.execute("""
    SELECT COUNT(*) FROM mirna_expression_bias e
    JOIN mirna_core_info c ON e.mirna_id = c.mirna_id
    WHERE e.species = 'Drosophila melanogaster'
""")
joined = cur.fetchone()[0]
cur.execute("""
    SELECT COUNT(*) FROM mirna_expression_bias WHERE species = 'Drosophila melanogaster'
""")
total_expr = cur.fetchone()[0]
print(f"  expression JOIN 匹配: {joined}/{total_expr}")
print(f"  {'✅ 完全匹配!' if total_expr == joined else '❌ 有缺失'}")

# 展示
print("\n📋 示例:")
cur.execute("""
    SELECT mirgenedb_id, mirbase_id, mirna_id FROM mirna_core_info
    WHERE species = 'Drosophila melanogaster' AND mirbase_id IS NOT NULL
    ORDER BY mirna_id
    LIMIT 10
""")
for r in cur.fetchall():
    print(f"  {r[0]}  →  mirbase={r[1]}  (mirna_id={r[2]})")

cur.close()
conn.close()
print("\n✨ 完成！")

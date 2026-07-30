"""Database connection and query functions for mirna_db."""

import psycopg2
import psycopg2.extras
from config import Config


def get_connection():
    """Get a new database connection."""
    return psycopg2.connect(Config.get_dsn())


def query_dict(sql, params=None):
    """Execute a query and return results as list of dicts."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(row) for row in rows]
    finally:
        conn.close()


def query_one(sql, params=None):
    """Execute a query and return a single dict, or None."""
    rows = query_dict(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    """Execute an INSERT/UPDATE/DELETE and commit."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── miRNA queries ──────────────────────────────────────────────────────────


def search_mirna(keyword, mode="contains", species=None):
    """
    Search mirna_core_info by mirna_id (exact) or mirna_id/species (contains).
    If species is provided, filter by that species.
    mode: 'exact' or 'contains'
    Returns results with display_id = COALESCE(mirbase_id, mirna_id).
    """
    if mode == "exact":
        sql = "SELECT *, COALESCE(mirbase_id, mirna_id) AS display_id FROM mirna_core_info WHERE mirna_id = %s"
        params = [keyword]
        if species and species != "all":
            sql += " AND species = %s"
            params.append(species)
        sql += " ORDER BY mirna_id"
        return query_dict(sql, params)
    else:
        sql = "SELECT *, COALESCE(mirbase_id, mirna_id) AS display_id FROM mirna_core_info WHERE (mirna_id ILIKE %s OR species ILIKE %s)"
        like = f"%{keyword}%"
        params = [like, like]
        if species and species != "all":
            sql += " AND species = %s"
            params.append(species)
        sql += " ORDER BY mirna_id"
        return query_dict(sql, params)


def count_mirna_total():
    """Total number of miRNA records."""
    row = query_one("SELECT COUNT(*) AS cnt FROM mirna_core_info")
    return row["cnt"] if row else 0


def count_tissue_samples():
    """Total distinct tissue/cell type samples."""
    row = query_one(
        "SELECT COUNT(DISTINCT context_name) AS cnt FROM mirna_expression_bias"
    )
    return row["cnt"] if row else 0


def count_species():
    """Total distinct species."""
    row = query_one("SELECT COUNT(DISTINCT species) AS cnt FROM mirna_core_info")
    return row["cnt"] if row else 0


def get_mirna_detail(mirna_id):
    """Get full detail for one miRNA."""
    core = query_one(
        "SELECT *, COALESCE(mirbase_id, mirna_id) AS display_id FROM mirna_core_info WHERE mirna_id = %s", (mirna_id,)
    )
    if not core:
        return None
    expression = query_dict(
        "SELECT *, COALESCE(mirbase_id, mirna_id) AS display_id FROM mirna_expression_bias WHERE mirna_id = %s ORDER BY context_type, context_name",
        (mirna_id,),
    )
    functional = query_dict(
        "SELECT * FROM mirna_functional_bias WHERE mirna_id = %s ORDER BY bias_category",
        (mirna_id,),
    )
    engineering = query_dict(
        "SELECT * FROM mirna_engineering_optimization WHERE mirna_id = %s ORDER BY opt_type",
        (mirna_id,),
    )
    interactions = query_dict(
        "SELECT * FROM mirna_target_interactions WHERE mirna_id = %s ORDER BY target_gene",
        (mirna_id,),
    )
    return {
        "core": core,
        "expression": expression,
        "functional": functional,
        "engineering": engineering,
        "interactions": interactions,
    }


def search_by_target_gene(gene_name):
    """
    Reverse search: given a target gene name, find all miRNA that target it.
    """
    sql = """
        SELECT i.*, c.species, c.mature_sequence, c.seed_sequence,
               c.mirbase_id, c.mirgenedb_id,
               COALESCE(c.mirbase_id, c.mirna_id) AS display_id
        FROM mirna_target_interactions i
        JOIN mirna_core_info c ON i.mirna_id = c.mirna_id
        WHERE i.target_gene ILIKE %s
        ORDER BY i.mirna_id
    """
    return query_dict(sql, (f"%{gene_name}%",))


def get_species_list():
    """Get list of distinct species for quick-entry buttons."""
    rows = query_dict(
        "SELECT DISTINCT species FROM mirna_core_info ORDER BY species"
    )
    return [r["species"] for r in rows]


# ── User queries ────────────────────────────────────────────────────────────


def create_user_table():
    """Create the users table if it doesn't exist."""
    sql = """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        );
    """
    execute(sql)


def create_user(username, email, password_hash):
    """Insert a new user. Returns the user id or raises on duplicate."""
    sql = """
        INSERT INTO users (username, email, password_hash)
        VALUES (%s, %s, %s)
        RETURNING id
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (username, email, password_hash))
            user_id = cur.fetchone()[0]
        conn.commit()
        return user_id
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_user_by_username(username):
    """Look up a user by username."""
    return query_one(
        "SELECT * FROM users WHERE username = %s", (username,)
    )


def get_user_by_email(email):
    """Look up a user by email."""
    return query_one("SELECT * FROM users WHERE email = %s", (email,))


def get_user_by_id(user_id):
    """Look up a user by primary key."""
    return query_one("SELECT * FROM users WHERE id = %s", (user_id,))

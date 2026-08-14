import sqlite3
import json
from pathlib import Path
from datetime import datetime

# Resolve database path relative to this file
BASE_DIR = Path(__file__).resolve().parent.parent
DB_DIR = BASE_DIR / "data"
DB_PATH = DB_DIR / "tracker.db"
SYSTEM_OWNER_KEY = "__system__"

def get_connection():
    """Returns a SQLite connection to tracker.db."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes SQLite database schema if tables do not exist."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Create runs table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            brand_domain TEXT NOT NULL,
            brand_name TEXT NOT NULL,
            country TEXT NOT NULL,
            language TEXT NOT NULL,
            competitors TEXT,
            high_volume_keywords TEXT,
            brand_keywords TEXT,
            use_dataforseo INTEGER DEFAULT 1,
            run_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    cursor.execute("PRAGMA table_info(runs)")
    run_columns = [col[1] for col in cursor.fetchall()]
    if "user_id" not in run_columns:
        try:
            cursor.execute("ALTER TABLE runs ADD COLUMN user_id INTEGER")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    if "competitors" not in run_columns:
        cursor.execute("ALTER TABLE runs ADD COLUMN competitors TEXT")
    if "high_volume_keywords" not in run_columns:
        cursor.execute("ALTER TABLE runs ADD COLUMN high_volume_keywords TEXT")
    if "brand_keywords" not in run_columns:
        cursor.execute("ALTER TABLE runs ADD COLUMN brand_keywords TEXT")
    if "use_dataforseo" not in run_columns:
        cursor.execute("ALTER TABLE runs ADD COLUMN use_dataforseo INTEGER DEFAULT 1")
    
    # Create mention_results table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mention_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            keyword TEXT NOT NULL,
            platform TEXT NOT NULL,
            mentioned BOOLEAN,
            mention_position INTEGER,
            sources_cited TEXT, -- JSON array of URLs
            competitor_mentions TEXT, -- JSON object: {domain: boolean}
            ai_response_text TEXT,
            response_status TEXT,
            error_category TEXT,
            error_message TEXT,
            has_valid_data INTEGER DEFAULT 0,
            retry_recommendation TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE
        )
    """)
    
    # Create competitor_metrics table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS competitor_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            domain TEXT NOT NULL,
            total_mentions INTEGER NOT NULL,
            avg_position REAL,
            share_of_voice REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS run_provider_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            payload_json TEXT,
            collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            agent_name TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            provenance_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_runs_user_id ON runs(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_provider_results_run_id ON run_provider_results(run_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_provider_results_run_id_provider ON run_provider_results(run_id, provider)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_results_run_id ON agent_results(run_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_results_user_id ON agent_results(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_results_run_id_agent ON agent_results(run_id, agent_name)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS google_ads_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            owner_key TEXT,
            owner_type TEXT,
            session_id TEXT,
            refresh_token_encrypted TEXT NOT NULL,
            token_expiry TEXT,
            scopes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS negative_keyword_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            owner_key TEXT,
            name TEXT NOT NULL,
            terms TEXT NOT NULL,
            classification TEXT NOT NULL,
            reason TEXT NOT NULL,
            confidence TEXT NOT NULL,
            risk TEXT NOT NULL,
            match_type TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS negative_keyword_settings_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            owner_key TEXT NOT NULL,
            custom_instructions TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS negative_keyword_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            owner_key TEXT,
            session_id TEXT,
            customer_id TEXT,
            campaign_id TEXT,
            campaign_name TEXT,
            negative_keyword TEXT,
            match_type TEXT,
            action_status TEXT NOT NULL,
            action_message TEXT,
            recommendation_snapshot TEXT,
            upstream_response TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS negative_keyword_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            owner_key TEXT NOT NULL,
            filename TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            source_type TEXT,
            summary_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    ensure_column(cursor, "google_ads_connections", "user_id", "INTEGER")
    ensure_column(cursor, "google_ads_connections", "owner_key", "TEXT")
    ensure_column(cursor, "google_ads_connections", "owner_type", "TEXT")
    ensure_column(cursor, "mention_results", "response_status", "TEXT")
    ensure_column(cursor, "mention_results", "error_category", "TEXT")
    ensure_column(cursor, "mention_results", "error_message", "TEXT")
    ensure_column(cursor, "mention_results", "has_valid_data", "INTEGER DEFAULT 0")
    ensure_column(cursor, "mention_results", "retry_recommendation", "TEXT")
    ensure_column(cursor, "negative_keyword_rules", "user_id", "INTEGER")
    ensure_column(cursor, "negative_keyword_rules", "owner_key", "TEXT")
    ensure_column(cursor, "negative_keyword_settings_v2", "user_id", "INTEGER")
    ensure_column(cursor, "negative_keyword_audit", "user_id", "INTEGER")
    ensure_column(cursor, "negative_keyword_audit", "owner_key", "TEXT")
    ensure_column(cursor, "negative_keyword_reports", "user_id", "INTEGER")
    backfill_owner_scope(cursor)
    migrate_negative_keyword_settings(cursor)

    conn.commit()
    conn.close()


def ensure_column(cursor, table_name: str, column_name: str, column_type: str):
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [col[1] for col in cursor.fetchall()]
    if column_name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def backfill_owner_scope(cursor):
    cursor.execute("UPDATE google_ads_connections SET owner_key = COALESCE(owner_key, session_id, ?) WHERE owner_key IS NULL OR owner_key = ''", (SYSTEM_OWNER_KEY,))
    cursor.execute("UPDATE google_ads_connections SET owner_type = COALESCE(owner_type, 'session') WHERE owner_type IS NULL OR owner_type = ''")
    cursor.execute("UPDATE negative_keyword_rules SET owner_key = COALESCE(owner_key, ?) WHERE owner_key IS NULL OR owner_key = ''", (SYSTEM_OWNER_KEY,))
    cursor.execute("UPDATE negative_keyword_audit SET owner_key = COALESCE(owner_key, session_id, ?) WHERE owner_key IS NULL OR owner_key = ''", (SYSTEM_OWNER_KEY,))


def migrate_negative_keyword_settings(cursor):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='negative_keyword_settings'")
    legacy_exists = cursor.fetchone()
    if not legacy_exists:
        return
    cursor.execute("SELECT custom_instructions, updated_at FROM negative_keyword_settings ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if row and row["custom_instructions"]:
        cursor.execute(
            """
            INSERT INTO negative_keyword_settings_v2 (owner_key, custom_instructions, updated_at)
            SELECT ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM negative_keyword_settings_v2 WHERE owner_key = ?
            )
            """,
            (SYSTEM_OWNER_KEY, row["custom_instructions"], row["updated_at"], SYSTEM_OWNER_KEY),
        )

def create_run(
    brand_domain,
    brand_name,
    country,
    language,
    competitors=None,
    use_dataforseo=True,
    high_volume_keywords=None,
    brand_keywords=None,
    user_id=None,
):
    """Inserts a new run and returns the run_id."""
    conn = get_connection()
    cursor = conn.cursor()
    competitors_payload = json.dumps(competitors) if competitors is not None else None
    high_volume_payload = json.dumps(high_volume_keywords) if high_volume_keywords is not None else None
    brand_keywords_payload = json.dumps(brand_keywords) if brand_keywords is not None else None
    cursor.execute(
        """
        INSERT INTO runs (
            user_id, brand_domain, brand_name, country, language, competitors,
            high_volume_keywords, brand_keywords, use_dataforseo, run_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            brand_domain,
            brand_name,
            country,
            language,
            competitors_payload,
            high_volume_payload,
            brand_keywords_payload,
            1 if use_dataforseo else 0,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
    )
    run_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return run_id

def insert_mention_result(
    run_id,
    keyword,
    platform,
    mentioned,
    mention_position,
    sources_cited,
    competitor_mentions,
    ai_response_text,
    response_status=None,
    error_category=None,
    error_message=None,
    has_valid_data=False,
    retry_recommendation=None,
):
    """Inserts a single API mention result."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO mention_results (
            run_id, keyword, platform, mentioned, mention_position, sources_cited,
            competitor_mentions, ai_response_text, response_status, error_category,
            error_message, has_valid_data, retry_recommendation, timestamp
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            keyword,
            platform,
            mentioned,
            mention_position,
            json.dumps(sources_cited) if sources_cited is not None else None,
            json.dumps(competitor_mentions) if competitor_mentions is not None else None,
            ai_response_text,
            response_status,
            error_category,
            error_message,
            1 if has_valid_data else 0,
            retry_recommendation,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )
    conn.commit()
    conn.close()

def insert_competitor_metrics(run_id, domain, total_mentions, avg_position, share_of_voice):
    """Inserts competitor share-of-voice metric for a run."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO competitor_metrics (run_id, domain, total_mentions, avg_position, share_of_voice)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, domain, total_mentions, avg_position, share_of_voice)
    )
    conn.commit()
    conn.close()


def upsert_run_provider_result(run_id, provider, status, payload=None, reason=None):
    """Stores or replaces a provider collection result for a run."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM run_provider_results WHERE run_id = ? AND provider = ?", (run_id, provider))
    cursor.execute(
        """
        INSERT INTO run_provider_results (run_id, provider, status, reason, payload_json, collected_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            provider,
            status,
            reason,
            json.dumps(payload) if payload is not None else None,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()


def get_run_provider_results(run_id):
    """Fetches provider collection results for a run."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM run_provider_results WHERE run_id = ? ORDER BY provider ASC, id ASC",
        (run_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    results = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item["payload_json"]) if item.get("payload_json") else None
        except (TypeError, ValueError):
            item["payload"] = None
        results.append(item)
    return results


def upsert_agent_result(run_id: int, user_id: int, agent_name: str, status: str, result: dict | None = None, provenance: dict | None = None):
    """Create or update the latest persisted result for one agent on an owned run."""
    conn = get_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result_json = json.dumps(result) if result is not None else None
    provenance_json = json.dumps(provenance) if provenance is not None else None
    cursor.execute(
        """
        SELECT id FROM agent_results
        WHERE run_id = ? AND user_id = ? AND agent_name = ?
        ORDER BY id DESC LIMIT 1
        """,
        (int(run_id), int(user_id), agent_name),
    )
    existing = cursor.fetchone()
    if existing:
        cursor.execute(
            """
            UPDATE agent_results
            SET status = ?, result_json = ?, provenance_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, result_json, provenance_json, timestamp, existing["id"]),
        )
        result_id = existing["id"]
    else:
        cursor.execute(
            """
            INSERT INTO agent_results
            (run_id, user_id, agent_name, status, result_json, provenance_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(run_id), int(user_id), agent_name, status, result_json, provenance_json, timestamp, timestamp),
        )
        result_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return result_id


def get_agent_results_for_run(run_id: int, user_id: int | None = None):
    """Fetch persisted agent results for a run, optionally scoped to a user."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id is None:
        cursor.execute(
            "SELECT * FROM agent_results WHERE run_id = ? ORDER BY updated_at DESC, id DESC",
            (int(run_id),),
        )
    else:
        cursor.execute(
            "SELECT * FROM agent_results WHERE run_id = ? AND user_id = ? ORDER BY updated_at DESC, id DESC",
            (int(run_id), int(user_id)),
        )
    rows = cursor.fetchall()
    conn.close()
    results = []
    for row in rows:
        item = dict(row)
        try:
            item["result"] = json.loads(item["result_json"]) if item.get("result_json") else None
        except (TypeError, ValueError):
            item["result"] = None
        try:
            item["provenance"] = json.loads(item["provenance_json"]) if item.get("provenance_json") else None
        except (TypeError, ValueError):
            item["provenance"] = None
        results.append(item)
    return results

def get_run(run_id):
    """Fetches a specific run record."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_run_for_user(run_id, user_id: int):
    """Fetches a specific run record owned by the authenticated user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM runs WHERE id = ? AND user_id = ?", (run_id, int(user_id)))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_latest_run():
    """Fetches the latest run."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_mention_results(run_id):
    """Fetches all mention results for a given run."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mention_results WHERE run_id = ?", (run_id,))
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        d = dict(r)
        d["sources_cited"] = json.loads(d["sources_cited"]) if d["sources_cited"] else []
        d["competitor_mentions"] = json.loads(d["competitor_mentions"]) if d["competitor_mentions"] else {}
        d["has_valid_data"] = bool(d.get("has_valid_data"))
        results.append(d)
    return results

def get_competitor_metrics(run_id):
    """Fetches competitor metrics for a run."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM competitor_metrics WHERE run_id = ?", (run_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_trend_data(brand_domain, competitor_domains):
    """
    Fetches historical brand and competitor mentions over all runs
    for the specified brand domain and competitors.
    Returns: list of dicts: [{'run_date': ..., 'brand': ..., 'competitor1': ...}]
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # We want to match runs where brand_domain matches the current brand_domain
    cursor.execute(
        "SELECT id, run_date FROM runs WHERE brand_domain = ? ORDER BY id ASC", 
        (brand_domain,)
    )
    runs = cursor.fetchall()
    
    trend = []
    for r in runs:
        run_id = r["id"]
        cursor.execute("SELECT COUNT(*) FROM mention_results WHERE run_id = ? AND has_valid_data = 1", (run_id,))
        valid_result_count = int(cursor.fetchone()[0] or 0)
        # Fetch competitor metrics for this run_id
        cursor.execute("SELECT domain, total_mentions FROM competitor_metrics WHERE run_id = ?", (run_id,))
        metrics = cursor.fetchall()
        
        metrics_dict = {m["domain"].lower(): m["total_mentions"] for m in metrics}
        
        # Populate run entry
        # Parse run_date for display format
        try:
            date_obj = datetime.strptime(r["run_date"], "%Y-%m-%d %H:%M:%S")
            formatted_date = date_obj.strftime("%b %d, %H:%M")
        except ValueError:
            formatted_date = r["run_date"]
            
        entry = {
            "run_date": formatted_date,
            "brand": metrics_dict.get(brand_domain.lower()) if valid_result_count > 0 else None
        }
        for comp in competitor_domains:
            entry[comp] = metrics_dict.get(comp.lower()) if valid_result_count > 0 else None
            
        trend.append(entry)
        
    conn.close()
    return trend


def create_user(email: str, password_hash: str):
    conn = get_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        INSERT INTO users (email, password_hash, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (email.lower().strip(), password_hash, timestamp, timestamp),
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return user_id


def get_user_by_email(email: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (email.strip(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (int(user_id),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def _scope_selector(user_id: int | None, owner_key: str | None):
    if user_id is not None:
        return "user_id = ?", (int(user_id),)
    return "owner_key = ?", (owner_key or SYSTEM_OWNER_KEY,)


def get_google_ads_connection(user_id: int | None = None, owner_key: str | None = None):
    """Fetch the Google Ads connection stored for an owner scope."""
    where_clause, params = _scope_selector(user_id, owner_key)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM google_ads_connections WHERE {where_clause}", params)
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_google_ads_connection(user_id: int | None, owner_key: str, refresh_token_encrypted, token_expiry=None, scopes=None, owner_type="session"):
    """Create or update the Google Ads connection for an owner scope."""
    conn = get_connection()
    cursor = conn.cursor()
    existing = get_google_ads_connection(user_id=user_id, owner_key=owner_key)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scopes_payload = json.dumps(scopes) if scopes is not None else None
    if existing:
        where_clause, scope_params = _scope_selector(user_id, owner_key)
        cursor.execute(
            f"""
            UPDATE google_ads_connections
            SET refresh_token_encrypted = ?, token_expiry = ?, scopes = ?, owner_type = ?, owner_key = ?, user_id = ?, updated_at = ?
            WHERE {where_clause}
            """,
            (refresh_token_encrypted, token_expiry, scopes_payload, owner_type, owner_key, user_id, timestamp, *scope_params),
        )
    else:
        cursor.execute(
            """
            INSERT INTO google_ads_connections (user_id, owner_key, owner_type, session_id, refresh_token_encrypted, token_expiry, scopes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, owner_key, owner_type, owner_key, refresh_token_encrypted, token_expiry, scopes_payload, timestamp, timestamp),
        )
    conn.commit()
    conn.close()


def delete_google_ads_connection(user_id: int | None = None, owner_key: str | None = None):
    """Delete the Google Ads connection for an owner scope."""
    where_clause, params = _scope_selector(user_id, owner_key)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM google_ads_connections WHERE {where_clause}", params)
    conn.commit()
    conn.close()


def get_negative_keyword_rules(user_id: int | None = None, owner_key: str | None = SYSTEM_OWNER_KEY):
    where_clause, params = _scope_selector(user_id, owner_key)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM negative_keyword_rules
        WHERE """ + where_clause + """
        ORDER BY priority DESC, id ASC
        """,
        params,
    )
    rows = cursor.fetchall()
    conn.close()
    results = []
    for row in rows:
        item = dict(row)
        item["terms"] = json.loads(item["terms"]) if item.get("terms") else []
        item["enabled"] = bool(item.get("enabled"))
        results.append(item)
    return results


def get_negative_keyword_rule_by_id(rule_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM negative_keyword_rules WHERE id = ?", (int(rule_id),))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    item["terms"] = json.loads(item["terms"]) if item.get("terms") else []
    item["enabled"] = bool(item.get("enabled"))
    return item


def get_scoped_negative_keyword_rule(rule_id: int, user_id: int | None = None, owner_key: str | None = SYSTEM_OWNER_KEY):
    where_clause, scope_params = _scope_selector(user_id, owner_key)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT * FROM negative_keyword_rules WHERE id = ? AND {where_clause}",
        (int(rule_id), *scope_params),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    item["terms"] = json.loads(item["terms"]) if item.get("terms") else []
    item["enabled"] = bool(item.get("enabled"))
    return item


def create_negative_keyword_rule(user_id: int | None, owner_key: str, rule: dict):
    conn = get_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        INSERT INTO negative_keyword_rules
        (user_id, owner_key, name, terms, classification, reason, confidence, risk, match_type, enabled, priority, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            owner_key,
            rule["name"],
            json.dumps(rule.get("terms") or []),
            rule["classification"],
            rule["reason"],
            rule["confidence"],
            rule["risk"],
            rule["match_type"],
            1 if rule.get("enabled", True) else 0,
            int(rule.get("priority", 0)),
            timestamp,
            timestamp,
        ),
    )
    rule_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return rule_id


def update_negative_keyword_rule(user_id: int | None, owner_key: str, rule_id: int, fields: dict):
    updates = []
    params = []
    for key in ("name", "classification", "reason", "confidence", "risk", "match_type", "priority"):
        if key in fields:
            updates.append(f"{key} = ?")
            params.append(fields[key])
    if "terms" in fields:
        updates.append("terms = ?")
        params.append(json.dumps(fields.get("terms") or []))
    if "enabled" in fields:
        updates.append("enabled = ?")
        params.append(1 if fields["enabled"] else 0)
    updates.append("updated_at = ?")
    params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    conn = get_connection()
    cursor = conn.cursor()
    where_clause, scope_params = _scope_selector(user_id, owner_key)
    cursor.execute(
        f"UPDATE negative_keyword_rules SET {', '.join(updates)} WHERE id = ? AND {where_clause}",
        params + [rule_id, *scope_params],
    )
    conn.commit()
    conn.close()


def delete_negative_keyword_rule(user_id: int | None, owner_key: str, rule_id: int):
    where_clause, scope_params = _scope_selector(user_id, owner_key)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM negative_keyword_rules WHERE id = ? AND {where_clause}", (rule_id, *scope_params))
    conn.commit()
    conn.close()


def reorder_negative_keyword_rules(user_id: int | None, owner_key: str, rule_ids: list[int]):
    conn = get_connection()
    cursor = conn.cursor()
    total = len(rule_ids)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    where_clause, scope_params = _scope_selector(user_id, owner_key)
    for index, rule_id in enumerate(rule_ids):
        priority = total - index
        cursor.execute(
            f"""
            UPDATE negative_keyword_rules
            SET priority = ?, updated_at = ?
            WHERE id = ? AND {where_clause}
            """,
            (priority, timestamp, int(rule_id), *scope_params),
        )
    conn.commit()
    conn.close()


def get_negative_keyword_instructions(user_id: int | None = None, owner_key: str | None = SYSTEM_OWNER_KEY):
    where_clause, params = _scope_selector(user_id, owner_key)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT custom_instructions, updated_at FROM negative_keyword_settings_v2 WHERE {where_clause} ORDER BY id DESC LIMIT 1",
        params,
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {"custom_instructions": "", "updated_at": None}
    return dict(row)


def set_negative_keyword_instructions(user_id: int | None, owner_key: str, custom_instructions: str):
    conn = get_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        INSERT INTO negative_keyword_settings_v2 (user_id, owner_key, custom_instructions, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, owner_key, custom_instructions, timestamp),
    )
    conn.commit()
    conn.close()


def create_negative_keyword_audit(record: dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO negative_keyword_audit
        (user_id, owner_key, session_id, customer_id, campaign_id, campaign_name, negative_keyword, match_type, action_status, action_message, recommendation_snapshot, upstream_response)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("user_id"),
            record.get("owner_key"),
            record.get("session_id"),
            record.get("customer_id"),
            record.get("campaign_id"),
            record.get("campaign_name"),
            record.get("negative_keyword"),
            record.get("match_type"),
            record.get("action_status"),
            record.get("action_message"),
            json.dumps(record.get("recommendation_snapshot")) if record.get("recommendation_snapshot") is not None else None,
            json.dumps(record.get("upstream_response")) if record.get("upstream_response") is not None else None,
        ),
    )
    audit_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return audit_id


def list_negative_keyword_audit(user_id: int | None = None, owner_key: str | None = SYSTEM_OWNER_KEY, limit: int = 100):
    where_clause, params = _scope_selector(user_id, owner_key)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM negative_keyword_audit
        WHERE """ + where_clause + """
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    )
    rows = cursor.fetchall()
    conn.close()
    results = []
    for row in rows:
        item = dict(row)
        item["recommendation_snapshot"] = json.loads(item["recommendation_snapshot"]) if item.get("recommendation_snapshot") else None
        item["upstream_response"] = json.loads(item["upstream_response"]) if item.get("upstream_response") else None
        results.append(item)
    return results


def create_negative_keyword_report(user_id: int | None, owner_key: str, filename: str, storage_path: str, source_type: str, summary_json: dict | None = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO negative_keyword_reports
        (user_id, owner_key, filename, storage_path, source_type, summary_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            owner_key,
            filename,
            storage_path,
            source_type,
            json.dumps(summary_json) if summary_json is not None else None,
        ),
    )
    report_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return report_id


def get_negative_keyword_report(user_id: int | None = None, owner_key: str | None = SYSTEM_OWNER_KEY, filename: str = ""):
    where_clause, params = _scope_selector(user_id, owner_key)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM negative_keyword_reports
        WHERE """ + where_clause + """ AND filename = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (*params, filename),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    item["summary_json"] = json.loads(item["summary_json"]) if item.get("summary_json") else None
    return item

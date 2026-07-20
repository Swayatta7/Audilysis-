import sqlite3
import json
from pathlib import Path
from datetime import datetime

# Resolve database path relative to this file
BASE_DIR = Path(__file__).resolve().parent.parent
DB_DIR = BASE_DIR / "data"
DB_PATH = DB_DIR / "tracker.db"

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
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_domain TEXT NOT NULL,
            brand_name TEXT NOT NULL,
            country TEXT NOT NULL,
            language TEXT NOT NULL,
            competitors TEXT,
            run_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("PRAGMA table_info(runs)")
    run_columns = [col[1] for col in cursor.fetchall()]
    if "competitors" not in run_columns:
        cursor.execute("ALTER TABLE runs ADD COLUMN competitors TEXT")
    
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
    
    conn.commit()
    conn.close()

def create_run(brand_domain, brand_name, country, language, competitors=None):
    """Inserts a new run and returns the run_id."""
    conn = get_connection()
    cursor = conn.cursor()
    competitors_payload = json.dumps(competitors) if competitors is not None else None
    cursor.execute(
        "INSERT INTO runs (brand_domain, brand_name, country, language, competitors, run_date) VALUES (?, ?, ?, ?, ?, ?)",
        (brand_domain, brand_name, country, language, competitors_payload, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    run_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return run_id

def insert_mention_result(run_id, keyword, platform, mentioned, mention_position, sources_cited, competitor_mentions, ai_response_text):
    """Inserts a single API mention result."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO mention_results (run_id, keyword, platform, mentioned, mention_position, sources_cited, competitor_mentions, ai_response_text, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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

def get_run(run_id):
    """Fetches a specific run record."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
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
            "brand": metrics_dict.get(brand_domain.lower(), 0)
        }
        for comp in competitor_domains:
            entry[comp] = metrics_dict.get(comp.lower(), 0)
            
        trend.append(entry)
        
    conn.close()
    return trend

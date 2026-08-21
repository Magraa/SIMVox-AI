import sqlite3
import datetime
from typing import List, Dict, Any, Optional
from config import DB_PATH, logger

class DatabaseManager:
    """Manages SQLite storage for call records, transcripts, and AI-generated summaries."""

    def __init__(self, db_path: str = str(DB_PATH)):
        self.db_path = db_path
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Initializes database schema if tables do not exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Table for call sessions
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT NOT NULL,
                call_type TEXT CHECK(call_type IN ('INCOMING', 'OUTGOING')),
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                duration_seconds INTEGER,
                summary TEXT,
                intent TEXT,
                audio_path TEXT,
                status TEXT DEFAULT 'COMPLETED'
            );
            """)
            # Migration check for existing DBs
            try:
                cursor.execute("ALTER TABLE calls ADD COLUMN audio_path TEXT")
            except Exception:
                pass


            # Table for real-time transcripts
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS call_transcripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id INTEGER NOT NULL,
                speaker TEXT CHECK(speaker IN ('CALLER', 'AI')),
                message TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(call_id) REFERENCES calls(id) ON DELETE CASCADE
            );
            """)
            conn.commit()
            logger.info("Database initialized successfully at %s", self.db_path)

    def start_call(self, phone_number: str, call_type: str = "INCOMING") -> int:
        """Logs the start of a call session and returns the generated call_id."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO calls (phone_number, call_type, start_time, status) VALUES (?, ?, ?, ?)",
                (phone_number, call_type, datetime.datetime.now().isoformat(), "IN_PROGRESS")
            )
            conn.commit()
            call_id = cursor.lastrowid
            logger.info("Started call session #%d for %s (%s)", call_id, phone_number, call_type)
            return call_id

    def add_transcript(self, call_id: int, speaker: str, message: str) -> None:
        """Records a transcript entry (CALLER or AI) for an active call."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO call_transcripts (call_id, speaker, message, timestamp) VALUES (?, ?, ?, ?)",
                (call_id, speaker, message, datetime.datetime.now().isoformat())
            )
            conn.commit()
            logger.debug("Logged transcript [#%d] [%s]: %s", call_id, speaker, message)

    def end_call(self, call_id: int, summary: Optional[str] = None, intent: Optional[str] = None, audio_path: Optional[str] = None) -> None:
        """Marks a call session as completed and records duration, summary, and audio recording path."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Fetch start time
            cursor.execute("SELECT start_time FROM calls WHERE id = ?", (call_id,))
            row = cursor.fetchone()
            if not row:
                logger.error("Call ID #%d not found", call_id)
                return

            start_time = datetime.datetime.fromisoformat(row["start_time"])
            end_time = datetime.datetime.now()
            duration = int((end_time - start_time).total_seconds())

            cursor.execute(
                """
                UPDATE calls 
                SET end_time = ?, duration_seconds = ?, summary = ?, intent = ?, audio_path = ?, status = 'COMPLETED'
                WHERE id = ?
                """,
                (end_time.isoformat(), duration, summary or "", intent or "", audio_path or "", call_id)
            )
            conn.commit()
            logger.info("Ended call session #%d. Duration: %ds", call_id, duration)


    def get_call_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieves recent call records."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM calls ORDER BY start_time DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_call_transcripts(self, call_id: int) -> List[Dict[str, Any]]:
        """Retrieves all dialogue lines for a specific call."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT speaker, message, timestamp FROM call_transcripts WHERE call_id = ? ORDER BY id ASC",
                (call_id,)
            )
            return [dict(row) for row in cursor.fetchall()]

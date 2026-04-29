#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Linux 微信普通消息监听器。"""

import hashlib
import logging
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

from decrypt_refresh import refresh_decrypted_databases

logger = logging.getLogger(__name__)

_RETRYABLE_DB_ERRORS = (
    "database disk image is malformed",
    "database is locked",
    "database schema has changed",
)


@dataclass
class DatabaseMessageResult:
    """数据库普通消息监听结果。"""

    message_id: str
    user_id: str
    user_name: str
    content: str
    timestamp: int
    message_type: str
    chat_id: str
    chat_name: str
    local_id: int
    local_type: int
    real_sender_id: int
    source: str = "linux_wechat_db"

    def to_payload(self) -> Dict[str, object]:
        return {
            "platform": "linux_wechat",
            "message_id": self.message_id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "content": self.content,
            "timestamp": self.timestamp,
            "message_type": self.message_type,
            "chat_id": self.chat_id,
            "chat_name": self.chat_name,
            "raw_data": {
                "source": self.source,
                "local_id": self.local_id,
                "local_type": self.local_type,
                "real_sender_id": self.real_sender_id,
            },
        }


class WeChatMessageDatabaseMonitor:
    """监听解密后的普通消息数据库。"""

    MESSAGE_TYPE_MAP = {
        1: "text",
        3: "image",
        34: "voice",
        43: "video",
    }

    def __init__(
        self,
        decrypt_project_dir: str,
        callback_url: str,
        callback: Optional[Callable[[DatabaseMessageResult], None]] = None,
        secret_key: Optional[str] = None,
        poll_interval: float = 2.0,
        include_message_types: Optional[List[str]] = None,
    ):
        self.decrypt_project_dir = Path(decrypt_project_dir)
        self.callback_url = callback_url
        self.callback = callback
        self.secret_key = secret_key
        self.poll_interval = poll_interval
        self.include_message_types = set(
            include_message_types or ["text", "image", "voice", "video"]
        )
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._source_candidates = self._build_source_candidates()
        self._last_source_state: Optional[tuple] = None
        self._pending_refresh_state: Optional[tuple] = None
        self._pending_refresh_since: Optional[float] = None
        self._refresh_debounce_seconds = 0.3
        self._refresh_min_interval_seconds = 0.8
        self._sleep_interval = min(self.poll_interval, 0.5)
        self._contact_names: Dict[str, str] = {}
        self._message_tables: List[Tuple[str, str]] = []
        self._last_seen_local_id: Dict[Tuple[str, str], int] = {}
        self._dedup_ids: Dict[str, float] = {}
        self._dedup_seconds = 600

    def _build_source_candidates(self) -> List[Path]:
        candidates: List[Path] = []
        config_path = self.decrypt_project_dir / "config.json"
        if config_path.exists():
            try:
                import json

                config_data = json.loads(config_path.read_text(encoding="utf-8"))
                db_dir = config_data.get("db_dir")
                if db_dir:
                    candidates.extend(
                        [
                            Path(db_dir) / "message" / "message_0.db",
                            Path(db_dir) / "message" / "message_0.db-wal",
                            Path(db_dir) / "session" / "session.db",
                            Path(db_dir) / "session" / "session.db-wal",
                        ]
                    )
            except Exception as exc:
                logger.warning("Failed to load message monitor config.json: %s", exc)
        return candidates

    def _get_source_state(self) -> Optional[tuple]:
        states = []
        for candidate in self._source_candidates:
            if not candidate.exists():
                continue
            stat = candidate.stat()
            states.append((str(candidate), stat.st_mtime_ns, stat.st_size))
        return tuple(states) if states else None

    def _load_contact_names(self):
        contact_db_path = (
            self.decrypt_project_dir / "decrypted" / "contact" / "contact.db"
        )
        if not contact_db_path.exists():
            return
        conn = sqlite3.connect(str(contact_db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT username, nick_name, remark FROM contact")
            mapping = {}
            for username, nick_name, remark in cur.fetchall():
                display_name = (remark or nick_name or username or "").strip()
                if username and display_name:
                    mapping[username] = display_name
            self._contact_names = mapping
        finally:
            conn.close()

    def _refresh_decrypted_db_if_needed(self) -> bool:
        source_state = self._get_source_state()
        if source_state is None:
            return False

        if self._last_source_state is None:
            self._last_source_state = source_state
            return False

        if source_state == self._last_source_state:
            self._pending_refresh_state = None
            self._pending_refresh_since = None
            return False

        if self._pending_refresh_state != source_state:
            self._pending_refresh_state = source_state
            self._pending_refresh_since = time.time()
            return False

        if self._pending_refresh_since is None:
            self._pending_refresh_since = time.time()
            return False

        if time.time() - self._pending_refresh_since < self._refresh_debounce_seconds:
            return False

        self._last_source_state = source_state
        self._pending_refresh_state = None
        self._pending_refresh_since = None
        logger.info(
            "Detected encrypted message DB change, refreshing decrypted databases..."
        )
        refreshed = refresh_decrypted_databases(
            self.decrypt_project_dir,
            min_interval_seconds=self._refresh_min_interval_seconds,
        )
        if refreshed:
            self._load_contact_names()
            self._load_message_tables()
            logger.info("Decrypted message databases refreshed")
            return True
        else:
            logger.info("Skipped duplicate message decrypt refresh")
        return False

    def _load_message_tables(self):
        message_db_path = (
            self.decrypt_project_dir / "decrypted" / "message" / "message_0.db"
        )
        if not message_db_path.exists():
            self._message_tables = []
            return
        conn = sqlite3.connect(str(message_db_path))
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%' ORDER BY name"
            )
            tables = [row[0] for row in cur.fetchall()]
            self._message_tables = [(str(message_db_path), table) for table in tables]
        finally:
            conn.close()

    def _load_name2id(self, conn: sqlite3.Connection) -> Dict[int, str]:
        try:
            rows = conn.execute("SELECT rowid, user_name FROM Name2Id").fetchall()
        except sqlite3.Error:
            return {}
        mapping = {}
        for rowid, user_name in rows:
            if user_name:
                mapping[rowid] = user_name
        return mapping

    def _cleanup_dedup(self):
        now = time.time()
        self._dedup_ids = {
            k: v for k, v in self._dedup_ids.items() if now - v < self._dedup_seconds
        }

    def _build_result(
        self,
        table_name: str,
        row: tuple,
        id_to_username: Dict[int, str],
    ) -> Optional[DatabaseMessageResult]:
        (
            local_id,
            local_type,
            create_time,
            real_sender_id,
            status,
            source,
            message_content,
        ) = row
        base_type = (
            local_type & 0xFFFFFFFF
            if local_type and local_type > 0xFFFFFFFF
            else local_type
        )
        message_type = self.MESSAGE_TYPE_MAP.get(base_type)
        if message_type not in self.include_message_types:
            return None

        user_id = id_to_username.get(real_sender_id, "")
        user_name = self._contact_names.get(user_id, user_id or "未知")

        if message_type == "text":
            content = message_content or ""
        elif message_type == "image":
            content = "[图片]"
        elif message_type == "voice":
            content = "[语音]"
        elif message_type == "video":
            content = "[视频]"
        else:
            return None

        if not user_id:
            user_id = user_name or "unknown"

        message_id = f"message_0:{table_name}:{local_id}"
        return DatabaseMessageResult(
            message_id=message_id,
            user_id=user_id,
            user_name=user_name,
            content=content,
            timestamp=int(create_time or 0),
            message_type="private",
            chat_id=user_id,
            chat_name=user_name,
            local_id=int(local_id),
            local_type=int(base_type or 0),
            real_sender_id=int(real_sender_id or 0),
        )

    def _post_message(self, result: DatabaseMessageResult):
        payload = result.to_payload()
        headers = {"Content-Type": "application/json"}
        if self.secret_key:
            payload["signature"] = self.secret_key
        response = requests.post(
            self.callback_url, json=payload, headers=headers, timeout=10
        )
        response.raise_for_status()

    def _scan_once(self):
        if not self._message_tables:
            return

        for db_path, table_name in self._message_tables:
            conn = sqlite3.connect(db_path)
            try:
                id_to_username = self._load_name2id(conn)
                last_seen = self._last_seen_local_id.get((db_path, table_name), 0)
                query = (
                    f"SELECT local_id, local_type, create_time, real_sender_id, status, source, message_content "
                    f'FROM "{table_name}" WHERE local_id > ? ORDER BY local_id ASC LIMIT 100'
                )
                cur = conn.cursor()
                try:
                    cur.execute(query, (last_seen,))
                except sqlite3.DatabaseError as exc:
                    message = str(exc).lower()
                    if any(item in message for item in _RETRYABLE_DB_ERRORS):
                        logger.warning(
                            "Message DB not ready yet, skip current scan: table=%s error=%s",
                            table_name,
                            exc,
                        )
                        continue
                    raise
                rows = cur.fetchall()
                for row in rows:
                    result = self._build_result(table_name, row, id_to_username)
                    local_id = int(row[0])
                    self._last_seen_local_id[(db_path, table_name)] = max(
                        local_id, self._last_seen_local_id.get((db_path, table_name), 0)
                    )
                    if not result:
                        continue
                    if result.message_id in self._dedup_ids:
                        continue
                    self._dedup_ids[result.message_id] = time.time()
                    self._cleanup_dedup()
                    logger.info(
                        "[MSG DETECTED] type=%s user=%s content=%s",
                        result.local_type,
                        result.user_name,
                        result.content,
                    )
                    if self.callback:
                        self.callback(result)
                    if self.callback_url:
                        self._post_message(result)
            finally:
                conn.close()

    def _establish_baseline(self):
        for db_path, table_name in self._message_tables:
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute(f'SELECT MAX(local_id) FROM "{table_name}"')
                max_local_id = cur.fetchone()[0] or 0
                self._last_seen_local_id[(db_path, table_name)] = int(max_local_id)
            finally:
                conn.close()
        logger.info(
            "Message DB baseline established for %s tables", len(self._message_tables)
        )

    def _run(self):
        logger.info(
            "Starting message DB monitor (interval: %ss, callback: %s)",
            self.poll_interval,
            self.callback_url or "<disabled>",
        )
        self._load_contact_names()
        self._load_message_tables()
        self._establish_baseline()
        while self.running:
            try:
                refreshed = self._refresh_decrypted_db_if_needed()
                self._scan_once()
                if refreshed:
                    self._scan_once()
            except Exception as exc:
                logger.error("Message DB monitor error: %s", exc, exc_info=True)
            time.sleep(self._sleep_interval)

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._run, name="message-db-monitor", daemon=True
        )
        self._thread.start()

    def stop(self):
        self.running = False

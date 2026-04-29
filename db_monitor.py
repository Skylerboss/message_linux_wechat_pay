#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解密后 session.db 监听器。"""

import logging
import os
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import zstandard as zstd

from decrypt_refresh import refresh_decrypted_databases

logger = logging.getLogger(__name__)


@dataclass
class DatabasePaymentResult:
    """数据库监听识别结果。"""

    amount: float
    payer: str
    timestamp: datetime
    username: str
    summary: str
    last_timestamp: int
    last_msg_type: int
    last_msg_sub_type: int
    sender_name: str
    source: str = "session_db"

    def to_dict(self) -> Dict[str, object]:
        return {
            "amount": self.amount,
            "payer": self.payer,
            "timestamp": self.timestamp.isoformat(),
            "username": self.username,
            "summary": self.summary,
            "last_timestamp": self.last_timestamp,
            "last_msg_type": self.last_msg_type,
            "last_msg_sub_type": self.last_msg_sub_type,
            "sender_name": self.sender_name,
            "source": self.source,
        }


class WeChatSessionDatabaseMonitor:
    """监听解密后的 session.db，识别收款助手与赞赏码摘要。"""

    AMOUNT_PATTERNS = [
        r"(\d+\.\d{2})\s*元",
        r"收款\s*(\d+\.\d{2})",
        r"到账\s*(\d+\.\d{2})",
    ]

    SERVICE_USERNAMES = {
        "brandservicesessionholder",
        "brandsessionholder",
        "mphelper",
    }

    def __init__(
        self,
        session_db_path: str,
        callback: Optional[Callable[[DatabasePaymentResult], None]] = None,
        decrypt_project_dir: Optional[str] = None,
        poll_interval: float = 2.0,
        summary_keywords: Optional[List[str]] = None,
        trusted_usernames: Optional[List[str]] = None,
        trusted_sender_names: Optional[List[str]] = None,
    ):
        self.session_db_path = Path(session_db_path)
        self.decrypt_project_dir = (
            Path(decrypt_project_dir) if decrypt_project_dir else None
        )
        self.callback = callback
        self.poll_interval = poll_interval
        self.summary_keywords = summary_keywords or [
            "收款",
            "到账",
            "二维码",
            "赞赏",
            "微信支付",
        ]
        self.trusted_usernames = set(trusted_usernames or ["brandservicesessionholder"])
        self.trusted_sender_names = set(trusted_sender_names or ["微信收款助手"])
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._recent_keys: Dict[str, float] = {}
        self._dedup_seconds = 300
        self._last_mtime_ns: Optional[int] = None
        self._startup_timestamp: int = int(time.time())
        self._baseline_ready = False
        self._source_candidates = self._build_source_candidates()
        self._last_source_state: Optional[tuple] = None
        self._pending_refresh_state: Optional[tuple] = None
        self._pending_refresh_since: Optional[float] = None
        self._refresh_debounce_seconds = 0.2
        self._refresh_min_interval_seconds = 0.5
        self._sleep_interval = min(self.poll_interval, 0.3)
        self._contact_names: Dict[str, str] = {}
        self._contact_db_path = self._build_contact_db_path()
        self._biz_message_db_path = self._build_biz_message_db_path()
        self._biz_message_tables: List[str] = []
        self._last_biz_message_timestamp: int = 0
        self._zstd = zstd.ZstdDecompressor()

    def _build_source_candidates(self) -> List[Path]:
        source_candidates: List[Path] = []
        if self.decrypt_project_dir:
            config_path = self.decrypt_project_dir / "config.json"
            if config_path.exists():
                try:
                    import json

                    config_data = json.loads(config_path.read_text(encoding="utf-8"))
                    db_dir = config_data.get("db_dir")
                    if db_dir:
                        source_candidates.extend(
                            [
                                Path(db_dir) / "session" / "session.db",
                                Path(db_dir) / "session" / "session.db-wal",
                            ]
                        )
                except Exception as exc:
                    logger.warning("Failed to load decrypt config.json: %s", exc)
        return source_candidates

    def _build_contact_db_path(self) -> Path:
        if self.decrypt_project_dir:
            return self.decrypt_project_dir / "decrypted" / "contact" / "contact.db"
        return Path("/root/wechat-decrypt/decrypted/contact/contact.db")

    def _build_biz_message_db_path(self) -> Path:
        if self.decrypt_project_dir:
            return (
                self.decrypt_project_dir / "decrypted" / "message" / "biz_message_0.db"
            )
        return Path("/root/wechat-decrypt/decrypted/message/biz_message_0.db")

    def _load_contact_names(self):
        if not self._contact_db_path.exists():
            return
        try:
            conn = sqlite3.connect(str(self._contact_db_path))
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
        except Exception as exc:
            logger.warning("Failed to load contact names: %s", exc)

    def _load_biz_message_tables(self):
        if not self._biz_message_db_path.exists():
            self._biz_message_tables = []
            return

        try:
            conn = sqlite3.connect(str(self._biz_message_db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%' ORDER BY name"
                )
                self._biz_message_tables = [row[0] for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Failed to load biz message tables: %s", exc)

    def _get_source_state(self) -> Optional[tuple]:
        states = []
        for candidate in self._source_candidates:
            if not candidate.exists():
                continue
            stat = candidate.stat()
            states.append((str(candidate), stat.st_mtime_ns, stat.st_size))
        if not states:
            return None
        return tuple(states)

    def _refresh_decrypted_db_if_needed(self) -> bool:
        if not self.decrypt_project_dir:
            return False

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
        logger.info("Detected encrypted DB change, refreshing decrypted databases...")
        try:
            refreshed = refresh_decrypted_databases(
                self.decrypt_project_dir,
                min_interval_seconds=self._refresh_min_interval_seconds,
            )
            if refreshed:
                logger.info("Decrypted databases refreshed")
                self._load_contact_names()
                self._load_biz_message_tables()
                self._last_mtime_ns = None
                return True
            else:
                logger.info("Skipped duplicate decrypt refresh")
        except Exception as exc:
            logger.error("Failed to refresh decrypted databases: %s", exc)
        return False

    def _cleanup_recent_keys(self):
        current = time.time()
        self._recent_keys = {
            key: ts
            for key, ts in self._recent_keys.items()
            if current - ts < self._dedup_seconds
        }

    def _normalize_summary(self, summary: str) -> str:
        if not summary:
            return ""
        return (
            summary.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if "\ufffd" in summary
            else summary
        )

    def _extract_amount(self, summary: str) -> Optional[float]:
        for pattern in self.AMOUNT_PATTERNS:
            match = re.search(pattern, summary)
            if not match:
                continue
            try:
                amount = float(match.group(1))
            except ValueError:
                continue
            if 0 < amount < 100000:
                return amount
        return None

    def _is_payment_summary(
        self, username: str, sender_name: str, summary: str
    ) -> bool:
        if not summary:
            return False

        if username in self.trusted_usernames:
            return True

        if sender_name and sender_name in self.trusted_sender_names:
            return True

        if username.startswith("gh_") and any(
            keyword in summary for keyword in self.summary_keywords
        ):
            return True

        return False

    def _is_service_username(self, username: str) -> bool:
        normalized_username = (username or "").strip()
        return (
            normalized_username.startswith("gh_")
            or normalized_username in self.SERVICE_USERNAMES
        )

    def _resolve_related_contact(
        self, payment_time: int, rows: List[tuple]
    ) -> Optional[str]:
        """尝试从同一时间段的普通会话中推断真实付款人。

        观察结果：
        - 收款/赞赏到账往往会先更新服务会话，如 `gh_*` / `brandservicesessionholder`
        - 同时，真实付款人的私聊会话时间戳会在附近更新
        - 这里使用最近 90 秒内、最接近到账时间的非服务会话作为低置信度候选
        """
        if not payment_time:
            return None

        candidates = []
        for row in rows:
            (
                username,
                summary,
                last_timestamp,
                sort_timestamp,
                last_msg_type,
                last_msg_sub_type,
                last_msg_sender,
                last_sender_display_name,
            ) = row

            candidate_username = (username or "").strip()
            if not candidate_username or self._is_service_username(candidate_username):
                continue

            candidate_time = int(last_timestamp or sort_timestamp or 0)
            if not candidate_time:
                continue

            delta = abs(payment_time - candidate_time)
            if delta > 90:
                continue

            candidates.append((delta, candidate_time, candidate_username))

        if not candidates:
            return None

        candidates.sort(key=lambda item: (item[0], -item[1]))
        return candidates[0][2]

    def _display_name_for_username(self, username: str) -> str:
        normalized_username = (username or "").strip()
        if not normalized_username:
            return ""
        return self._contact_names.get(normalized_username, normalized_username)

    def _decode_blob_to_text(self, value) -> str:
        if value is None:
            return ""
        data = value.tobytes() if isinstance(value, memoryview) else value
        if isinstance(data, str):
            return data
        if not isinstance(data, (bytes, bytearray)):
            return str(data)

        try:
            return self._zstd.decompress(data).decode("utf-8", errors="replace")
        except Exception:
            pass

        try:
            return bytes(data).decode("utf-8", errors="replace")
        except Exception:
            return ""

    def _extract_payer_from_payment_xml(self, xml_text: str) -> Optional[str]:
        if not xml_text or "<msg" not in xml_text:
            return None

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None

        description = (
            root.findtext(".//appmsg/des")
            or root.findtext(".//appmsg/mmreader/category/topnew/digest")
            or root.findtext(".//appmsg/mmreader/category/item/digest")
            or ""
        )
        if not description:
            return None

        description = description.replace("\r", "")
        match = re.search(r"来自\s*([^\n]+)", description)
        if not match:
            return None

        payer = match.group(1).strip()
        return payer or None

    def _extract_summary_from_payment_xml(self, xml_text: str) -> str:
        if not xml_text or "<msg" not in xml_text:
            return ""

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return ""

        summary = (
            root.findtext(".//appmsg/title")
            or root.findtext(".//appmsg/des")
            or root.findtext(".//appmsg/mmreader/category/topnew/digest")
            or root.findtext(".//appmsg/mmreader/category/item/digest")
            or ""
        )
        return self._normalize_summary(summary.replace("\r", "").strip())

    def _build_cross_source_payment_key(
        self, amount: float, payer: str, payment_time: int
    ) -> str:
        normalized_payer = (payer or "未知").strip() or "未知"
        time_bucket = int((payment_time or int(time.time())) / 15)
        return f"payment::{amount:.2f}::{normalized_payer}::{time_bucket}"

    def _build_cross_source_amount_key(self, amount: float, payment_time: int) -> str:
        time_bucket = int((payment_time or int(time.time())) / 15)
        return f"payment_amount::{amount:.2f}::{time_bucket}"

    def _mark_payment_keys(self, *keys: str):
        now = time.time()
        for key in keys:
            if key:
                self._recent_keys[key] = now
        self._cleanup_recent_keys()

    def _establish_biz_message_baseline(self):
        if not self._biz_message_db_path.exists():
            return

        if not self._biz_message_tables:
            self._load_biz_message_tables()
            if not self._biz_message_tables:
                return

        latest_timestamp = 0
        try:
            conn = sqlite3.connect(str(self._biz_message_db_path))
            try:
                cur = conn.cursor()
                for table_name in self._biz_message_tables:
                    try:
                        cur.execute(f'SELECT MAX(create_time) FROM "{table_name}"')
                        max_timestamp = int(cur.fetchone()[0] or 0)
                    except Exception:
                        continue
                    latest_timestamp = max(latest_timestamp, max_timestamp)
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Failed to establish biz message baseline: %s", exc)
            return

        self._last_biz_message_timestamp = latest_timestamp
        if latest_timestamp:
            logger.info(
                "Biz message baseline established at timestamp=%s",
                latest_timestamp,
            )

    def _scan_biz_message_once(self):
        if not self._biz_message_db_path.exists():
            return

        if not self._biz_message_tables:
            self._load_biz_message_tables()
            if not self._biz_message_tables:
                return

        try:
            conn = sqlite3.connect(str(self._biz_message_db_path))
            try:
                cur = conn.cursor()
                latest_seen_timestamp = self._last_biz_message_timestamp

                for table_name in self._biz_message_tables:
                    try:
                        cur.execute(
                            f'''SELECT create_time, local_type, message_content
                                FROM "{table_name}"
                                WHERE create_time > ?
                                ORDER BY create_time ASC
                                LIMIT 50''',
                            (self._last_biz_message_timestamp,),
                        )
                        rows = cur.fetchall()
                    except Exception:
                        continue

                    for create_time, local_type, message_content in rows:
                        event_time = int(create_time or 0)
                        if event_time > latest_seen_timestamp:
                            latest_seen_timestamp = event_time

                        base_type = int(local_type) & 0xFFFFFFFF if local_type else 0
                        if base_type != 49:
                            continue

                        xml_text = self._decode_blob_to_text(message_content)
                        if not xml_text:
                            continue

                        summary = self._extract_summary_from_payment_xml(xml_text)
                        amount = self._extract_amount(summary or xml_text)
                        if amount is None:
                            continue

                        payer = self._extract_payer_from_payment_xml(xml_text) or "未知"
                        payment_time = event_time or int(time.time())
                        dedup_key = (
                            f"biz::{table_name}::{payment_time}::{amount:.2f}::{payer}"
                        )
                        cross_source_key = self._build_cross_source_payment_key(
                            amount, payer, payment_time
                        )
                        cross_source_amount_key = self._build_cross_source_amount_key(
                            amount, payment_time
                        )
                        if (
                            dedup_key in self._recent_keys
                            or cross_source_key in self._recent_keys
                            or cross_source_amount_key in self._recent_keys
                        ):
                            continue

                        self._mark_payment_keys(
                            dedup_key, cross_source_key, cross_source_amount_key
                        )

                        result = DatabasePaymentResult(
                            amount=amount,
                            payer=payer,
                            timestamp=datetime.fromtimestamp(payment_time),
                            username="gh_biz_message",
                            summary=summary or f"支付到账{amount:.2f}元",
                            last_timestamp=payment_time,
                            last_msg_type=base_type,
                            last_msg_sub_type=5,
                            sender_name=payer,
                            source="biz_message_db",
                        )
                        logger.info(
                            "[BIZ DB DETECTED] %s元 from %s table=%s summary=%s",
                            result.amount,
                            result.payer,
                            table_name,
                            result.summary,
                        )
                        if self.callback:
                            self.callback(result)

                self._last_biz_message_timestamp = latest_seen_timestamp
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Failed to scan biz message DB: %s", exc)

    def _resolve_payer_from_biz_message(
        self, payment_time: int, amount: float
    ) -> Optional[str]:
        if not self._biz_message_db_path.exists() or not payment_time:
            return None

        try:
            conn = sqlite3.connect(str(self._biz_message_db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%' ORDER BY name"
                )
                tables = [row[0] for row in cur.fetchall()]
                for table_name in tables:
                    try:
                        cur.execute(
                            f'''SELECT create_time, local_type, message_content
                                FROM "{table_name}"
                                WHERE ABS(create_time - ?) <= 10
                                ORDER BY ABS(create_time - ?), create_time DESC
                                LIMIT 20''',
                            (payment_time, payment_time),
                        )
                        rows = cur.fetchall()
                    except Exception:
                        continue

                    for create_time, local_type, message_content in rows:
                        base_type = int(local_type) & 0xFFFFFFFF if local_type else 0
                        if base_type != 49:
                            continue
                        xml_text = self._decode_blob_to_text(message_content)
                        if not xml_text:
                            continue
                        if (
                            f"{amount:.2f}" not in xml_text
                            and f"{amount:.1f}" not in xml_text
                        ):
                            continue

                        payer = self._extract_payer_from_payment_xml(xml_text)
                        if payer:
                            return payer
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Failed to resolve payer from biz message DB: %s", exc)

        return None

    def _resolve_payer(
        self,
        username: str,
        sender_name: str,
        payment_time: int,
        rows: List[tuple],
        amount: float,
    ) -> str:
        """解析付款人显示名。

        当前 session 摘要层的 `gh_*`、`brandservicesessionholder` 等多为服务会话，
        不是最终付款人的真实账号。因此：
        - 优先使用明确的人类可读 sender_name
        - 再尝试根据最近会话时间戳推断真实联系人
        - 最后服务会话统一回退为“未知”
        """
        normalized_sender = (sender_name or "").strip()
        normalized_username = (username or "").strip()

        if normalized_sender and normalized_sender not in self.trusted_sender_names:
            return normalized_sender

        biz_payer = self._resolve_payer_from_biz_message(payment_time, amount)
        if biz_payer:
            return biz_payer

        related_contact = self._resolve_related_contact(payment_time, rows)
        if related_contact:
            return self._display_name_for_username(related_contact)

        if self._is_service_username(normalized_username):
            return "未知"

        return normalized_sender or normalized_username or "未知"

    def _scan_once(self):
        if not self.session_db_path.exists():
            logger.warning("Session DB not found: %s", self.session_db_path)
            return

        stat = self.session_db_path.stat()
        if self._last_mtime_ns is not None and stat.st_mtime_ns == self._last_mtime_ns:
            return
        self._last_mtime_ns = stat.st_mtime_ns

        conn = sqlite3.connect(str(self.session_db_path))
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT username, summary, last_timestamp, sort_timestamp,
                       last_msg_type, last_msg_sub_type, last_msg_sender,
                       last_sender_display_name
                FROM SessionTable
                ORDER BY sort_timestamp DESC
                LIMIT 100
                """
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        current_scan_keys = []
        for row in rows:
            (
                username,
                summary,
                last_timestamp,
                sort_timestamp,
                last_msg_type,
                last_msg_sub_type,
                last_msg_sender,
                last_sender_display_name,
            ) = row
            summary = self._normalize_summary(summary or "")
            sender_name = self._normalize_summary(last_sender_display_name or "")
            if not self._is_payment_summary(username or "", sender_name, summary):
                continue
            if last_msg_type != 49 or last_msg_sub_type != 5:
                continue

            amount = self._extract_amount(summary)
            if amount is None:
                continue

            payment_time = int(last_timestamp or sort_timestamp or 0)
            dedup_key = f"db::{username}::{payment_time}::{amount:.2f}::{summary}"
            normalized_summary = re.sub(r"\s+", "", summary)
            summary_key = (
                f"db_summary::{payment_time}::{amount:.2f}::{normalized_summary}"
            )
            resolved_payer = self._resolve_payer(
                username or "", sender_name, payment_time, rows, amount
            )
            cross_source_key = self._build_cross_source_payment_key(
                amount, resolved_payer, payment_time
            )
            cross_source_amount_key = self._build_cross_source_amount_key(
                amount, payment_time
            )
            current_scan_keys.append(dedup_key)
            current_scan_keys.append(summary_key)

            if not self._baseline_ready:
                continue

            if payment_time and payment_time < self._startup_timestamp:
                continue

            if (
                dedup_key in self._recent_keys
                or summary_key in self._recent_keys
                or cross_source_key in self._recent_keys
                or cross_source_amount_key in self._recent_keys
            ):
                continue

            self._mark_payment_keys(
                dedup_key,
                summary_key,
                cross_source_key,
                cross_source_amount_key,
            )

            result = DatabasePaymentResult(
                amount=amount,
                payer=resolved_payer,
                timestamp=datetime.fromtimestamp(payment_time)
                if payment_time
                else datetime.now(),
                username=username or "",
                summary=summary,
                last_timestamp=payment_time,
                last_msg_type=last_msg_type or 0,
                last_msg_sub_type=last_msg_sub_type or 0,
                sender_name=sender_name,
            )
            logger.info(
                "[DB DETECTED] %s元 from %s username=%s summary=%s",
                result.amount,
                result.payer,
                result.username,
                result.summary,
            )
            if self.callback:
                self.callback(result)

        if not self._baseline_ready:
            now = time.time()
            for key in current_scan_keys:
                self._recent_keys[key] = now
            self._cleanup_recent_keys()
            self._baseline_ready = True
            logger.info(
                "Session DB baseline established with %s existing payment summaries",
                len(current_scan_keys),
            )

    def _run(self):
        logger.info(
            "Starting session DB monitor (interval: %ss, db: %s)",
            self.poll_interval,
            self.session_db_path,
        )
        while self.running:
            try:
                refreshed = self._refresh_decrypted_db_if_needed()
                self._scan_biz_message_once()
                self._scan_once()
                if refreshed:
                    self._scan_biz_message_once()
                    self._scan_once()
            except Exception as exc:
                logger.error("Session DB monitor error: %s", exc, exc_info=True)
            time.sleep(self._sleep_interval)

    def start(self):
        if self.running:
            return
        self._load_contact_names()
        self._load_biz_message_tables()
        self._establish_biz_message_baseline()
        self.running = True
        self._thread = threading.Thread(
            target=self._run, name="session-db-monitor", daemon=True
        )
        self._thread.start()

    def stop(self):
        self.running = False

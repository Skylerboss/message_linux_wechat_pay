#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解密数据库刷新协调工具。"""

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

_LOCKS_GUARD = threading.Lock()
_PROJECT_LOCKS: Dict[str, threading.Lock] = {}
_LAST_REFRESH_AT: Dict[str, float] = {}


def _get_project_lock(project_dir: Path) -> threading.Lock:
    normalized_dir = str(project_dir.resolve())
    with _LOCKS_GUARD:
        lock = _PROJECT_LOCKS.get(normalized_dir)
        if lock is None:
            lock = threading.Lock()
            _PROJECT_LOCKS[normalized_dir] = lock
        return lock


def refresh_decrypted_databases(
    project_dir: Path,
    *,
    min_interval_seconds: float = 0.8,
    python_bin: str = "python3",
) -> bool:
    """刷新解密数据库，同一项目目录下做线程级去重。"""
    normalized_dir = str(project_dir.resolve())
    project_lock = _get_project_lock(project_dir)

    acquired = project_lock.acquire(blocking=False)
    if not acquired:
        logger.debug("Decrypt refresh already in progress: %s", normalized_dir)
        return False

    try:
        now = time.time()
        last_refresh_at = _LAST_REFRESH_AT.get(normalized_dir, 0.0)
        if now - last_refresh_at < min_interval_seconds:
            logger.debug(
                "Skipping duplicate decrypt refresh within %.2fs: %s",
                min_interval_seconds,
                normalized_dir,
            )
            return False

        subprocess.run(
            [python_bin, "decrypt_db.py"],
            cwd=str(project_dir),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _LAST_REFRESH_AT[normalized_dir] = time.time()
        return True
    finally:
        project_lock.release()

#!/usr/bin/env python3
"""共享工具模块：代理配置、config 加载、DB 统计、后台任务管理"""

import configparser
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
CONFIG_PATH = ROOT / "config.ini"
FUNDING_DB = ROOT / "db" / "funding_rate.db"
BFUSD_DB = ROOT / "db" / "bfusd.db"


def get_proxies():
    """获取代理配置：环境变量 > config.ini
    Returns dict {"http": proxy_url, "https": proxy_url} or None
    """
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not proxy:
        try:
            cfg = configparser.ConfigParser()
            cfg.read(str(CONFIG_PATH))
            proxy = cfg.get("proxy", "url", fallback="").strip()
        except Exception:
            pass
    return {"http": proxy, "https": proxy} if proxy else None


def load_config():
    """加载 config.ini，返回 ConfigParser 对象"""
    config = configparser.ConfigParser()
    config.optionxform = str  # 保留 key 原始大小写
    config.read(str(CONFIG_PATH))
    return config


def get_pairs(config):
    """从 config 的 [pairs] 段获取交易对列表
    Returns dict: {"btcusdt": "BTCUSDT", "ethusdt": "ETHUSDT", ...}
    """
    pairs = {}
    for name, _ in config.items("pairs"):
        pairs[name.lower()] = name
    return pairs


def get_pair_stats(conn):
    """获取 funding_rate 数据库中每个交易对的数据范围统计
    Returns list of dicts:
      [{"pair": "BTCUSDT", "min_date": "2019-09-10 16:00:00",
        "max_date": "2026-05-02 00:00:00", "count": 7277}, ...]
    """
    rows = conn.execute(
        "SELECT pair, MIN(time), MAX(time), COUNT(*) "
        "FROM funding_rate GROUP BY pair ORDER BY pair"
    ).fetchall()
    return [
        {"pair": r[0], "min_date": r[1], "max_date": r[2], "count": r[3]}
        for r in rows
    ]


def get_bfusd_stats(conn):
    """获取 bfusd_rate 数据库的数据范围统计
    Returns dict: {"min_date": "2024-11-20", "max_date": "2026-05-02", "count": 529}
    """
    row = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(*) FROM bfusd_rate"
    ).fetchone()
    return {
        "min_date": row[0], "max_date": row[1], "count": row[2]
    }


# ---- 后台任务管理 ----
# 使用文件存储 job 状态，以支持 uWSGI 多进程模式

JOBS_DIR = ROOT / "uwsgi" / "jobs"


def _ensure_jobs_dir():
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _job_path(job_id):
    return JOBS_DIR / f"{job_id}.json"


def _save_job(job_id, data):
    _ensure_jobs_dir()
    with open(_job_path(job_id), "w") as f:
        json.dump(data, f)


def _load_job(job_id):
    path = _job_path(job_id)
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


# 内存中的计数器（用于生成 job_id，不影响正确性）
_job_counter = 0
_counter_lock = threading.Lock()


def create_job(job_type, pair=None):
    global _job_counter
    with _counter_lock:
        _job_counter += 1
        job_id = f"job_{_job_counter}"
    data = {
        "job_id": job_id,
        "job_type": job_type,
        "pair": pair,
        "status": "running",
        "progress": 0,
        "message": "正在获取数据...",
        "new_records": 0,
        "error": None,
    }
    _save_job(job_id, data)
    return data


def update_job(job_id, **kwargs):
    """更新 job 的某些字段"""
    data = _load_job(job_id)
    if data:
        data.update(kwargs)
        _save_job(job_id, data)


def get_job(job_id):
    return _load_job(job_id)


def get_active_jobs():
    _ensure_jobs_dir()
    jobs = []
    for path in sorted(JOBS_DIR.glob("*.json")):
        data = _load_job(path.stem)
        if data:
            jobs.append(data)
    return jobs


def safe_float(value, default=0.0):
    """安全地将值转换为 float，失败时返回默认值"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def pct(value, total):
    """计算百分比，避免除零错误"""
    return (value / total * 100) if total else 0

#!/usr/bin/env python3
"""初始化数据库

克隆代码后首次运行前执行此脚本，创建所需的数据库文件和表结构。

用法:
    python scripts/initdb.py
"""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "db"
DBS = {
    "funding_rate.db": ("""
        CREATE TABLE IF NOT EXISTS funding_rate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT NOT NULL,
            time TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            funding_rate TEXT NOT NULL,
            price TEXT NOT NULL
        )
    """, [
        "CREATE INDEX IF NOT EXISTS idx_pair ON funding_rate(pair)",
        "CREATE INDEX IF NOT EXISTS idx_timestamp ON funding_rate(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_pair_ts ON funding_rate(pair, timestamp)",
    ]),
    "bfusd.db": ("""
        CREATE TABLE IF NOT EXISTS bfusd_rate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            apr REAL NOT NULL
        )
    """, [
        "CREATE INDEX IF NOT EXISTS idx_date ON bfusd_rate(date)",
    ]),
    "rwusd.db": ("""
        CREATE TABLE IF NOT EXISTS rwusd_rate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            apr REAL NOT NULL
        )
    """, [
        "CREATE INDEX IF NOT EXISTS idx_date ON rwusd_rate(date)",
    ]),
    "ldusdt.db": ("""
        CREATE TABLE IF NOT EXISTS ldusdt_rate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            apr REAL NOT NULL
        )
    """, [
        "CREATE INDEX IF NOT EXISTS idx_date ON ldusdt_rate(date)",
    ]),
}


def init_db(filename, ddl, indexes):
    """创建单个数据库及表结构"""
    db_path = DB_DIR / filename
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(ddl)
        for idx in indexes:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()
    print(f"  ✓ {db_path}")


def main():
    print("初始化数据库...")
    DB_DIR.mkdir(parents=True, exist_ok=True)
    for filename, (ddl, indexes) in DBS.items():
        init_db(filename, ddl, indexes)
    print("完成。现在可以运行 fetch 脚本获取数据，或直接启动 WebUI。")
    print()
    print("  启动 WebUI:  python app.py")
    print("  获取数据:   python scripts/fetch_funding_rate_db.py")
    print("  从 CSV 导入: python scripts/import_data.py")


if __name__ == "__main__":
    main()

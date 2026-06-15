#!/usr/bin/env python3
"""初始化数据库

克隆代码后首次运行前执行此脚本，创建所需的数据库文件和表结构。

用法:
    python initdb.py
"""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FUNDING_DB = ROOT / "db" / "funding_rate.db"
BFUSD_DB = ROOT / "db" / "bfusd.db"


def init_funding_rate_db():
    """创建资金费率数据库及表结构"""
    FUNDING_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(FUNDING_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS funding_rate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT NOT NULL,
            time TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            funding_rate TEXT NOT NULL,
            price TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pair ON funding_rate(pair)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON funding_rate(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pair_ts ON funding_rate(pair, timestamp)")
    conn.commit()
    conn.close()
    print(f"  ✓ {FUNDING_DB}")


def init_bfusd_db():
    """创建 BFUSD 年化利率数据库及表结构"""
    BFUSD_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(BFUSD_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bfusd_rate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            apr REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON bfusd_rate(date)")
    conn.commit()
    conn.close()
    print(f"  ✓ {BFUSD_DB}")


def main():
    print("初始化数据库...")
    init_funding_rate_db()
    init_bfusd_db()
    print("完成。现在可以运行 fetch 脚本获取数据，或直接启动 WebUI。")
    print()
    print("  获取数据:  python scripts/fetch_funding_rate_db.py")
    print("  启动 WebUI: uwsgi --ini uwsgi/uwsgi.ini")
    print("            python app.py")


if __name__ == "__main__":
    main()

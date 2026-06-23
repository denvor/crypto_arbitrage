#!/usr/bin/env python3
"""导出数据库数据到 CSV 文件

将 funding_rate.db 按交易对导出，收益型资产数据库各自独立导出。
文件保存到 db/ 目录，格式为 CSV。
"""

import csv
import sqlite3
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DB_DIR = ROOT / "db"
FUNDING_DB = DB_DIR / "funding_rate.db"

# 收益型资产数据库配置
YIELD_DBS = [
    {"name": "bfusd", "db": DB_DIR / "bfusd.db", "table": "bfusd_rate"},
    {"name": "rwusd", "db": DB_DIR / "rwusd.db", "table": "rwusd_rate"},
    {"name": "ldusdt", "db": DB_DIR / "ldusdt.db", "table": "ldusdt_rate"},
]


def export_funding_rate():
    """按交易对导出资金费率数据"""
    if not FUNDING_DB.exists():
        print(f"错误: 数据库文件不存在 {FUNDING_DB}")
        return False

    conn = sqlite3.connect(str(FUNDING_DB))
    try:
        pairs = conn.execute("SELECT DISTINCT pair FROM funding_rate ORDER BY pair").fetchall()

        for (pair,) in pairs:
            rows = conn.execute(
                "SELECT pair, time, timestamp, funding_rate, price "
                "FROM funding_rate WHERE pair = ? ORDER BY timestamp",
                (pair,),
            ).fetchall()

            filename = DB_DIR / f"funding_rate_{pair}.csv"
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["pair", "time", "timestamp", "funding_rate", "price"])
                writer.writerows(rows)

            print(f"  ✓ {pair}: {len(rows)} 条 → {filename.name}")

    finally:
        conn.close()
    return True


def export_yield_rates():
    """导出收益型资产利率数据"""
    for cfg in YIELD_DBS:
        if not cfg["db"].exists():
            print(f"  跳过 {cfg['name']}: 数据库文件不存在")
            continue

        conn = sqlite3.connect(str(cfg["db"]))
        try:
            rows = conn.execute(
                f"SELECT date, apr FROM {cfg['table']} ORDER BY date"
            ).fetchall()
        finally:
            conn.close()

        filename = DB_DIR / f"{cfg['name']}_rate.csv"
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "apr"])
            writer.writerows(rows)

        print(f"  ✓ {cfg['name']}: {len(rows)} 条 → {filename.name}")


def main():
    print("导出资金费率数据 ...")
    export_funding_rate()

    print("\n导出收益型资产利率数据 ...")
    export_yield_rates()

    print("\n完成。CSV 文件保存在 db/ 目录：")
    for f in sorted(DB_DIR.glob("*.csv")):
        size = f.stat().st_size
        print(f"  {f.name} ({size:,} bytes)")


if __name__ == "__main__":
    main()

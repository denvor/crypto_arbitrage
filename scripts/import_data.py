#!/usr/bin/env python3
"""从 CSV 文件导入数据到数据库

支持按交易对/资产单独导入，或一次导入全部数据。
自动跳过重复记录（幂等）。
"""

import argparse
import csv
import sqlite3
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DB_DIR = ROOT / "db"
FUNDING_DB = DB_DIR / "funding_rate.db"

# 收益型资产数据库配置
YIELD_DBS = {
    "bfusd":  {"name": "bfusd",  "db": DB_DIR / "bfusd.db",  "table": "bfusd_rate"},
    "rwusd":  {"name": "rwusd",  "db": DB_DIR / "rwusd.db",  "table": "rwusd_rate"},
    "ldusdt": {"name": "ldusdt", "db": DB_DIR / "ldusdt.db", "table": "ldusdt_rate"},
}


def ensure_table(conn, db_type):
    """确保目标表存在"""
    if db_type == "funding_rate":
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
    else:
        # 收益型资产表
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {db_type} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                apr REAL NOT NULL
            )
        """)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_date ON {db_type}(date)")


def import_funding_rate(pair=None):
    """导入资金费率 CSV 数据

    Args:
        pair: 交易对名称（如 BTCUSDT），为 None 时导入所有存在的 CSV
    """
    if pair:
        csv_files = [DB_DIR / f"funding_rate_{pair}.csv"]
    else:
        csv_files = sorted(DB_DIR.glob("funding_rate_*.csv"))

    if not csv_files:
        print("  没有找到资金费率 CSV 文件")
        return

    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(FUNDING_DB))
    ensure_table(conn, "funding_rate")

    total_imported = 0
    for csv_path in csv_files:
        if not csv_path.exists():
            print(f"  文件不存在: {csv_path.name}")
            continue

        # 从文件名提取交易对
        pair_name = csv_path.stem.replace("funding_rate_", "")

        # 加载已存在的 timestamp 到 set，用于去重
        existing = set(
            r[0] for r in conn.execute(
                "SELECT timestamp FROM funding_rate WHERE pair = ?", (pair_name,)
            ).fetchall()
        )

        imported = 0
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = int(row["timestamp"])
                    if ts in existing:
                        continue
                    conn.execute(
                        "INSERT INTO funding_rate (pair, time, timestamp, funding_rate, price) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (row["pair"], row["time"], ts, row["funding_rate"], row["price"]),
                    )
                    imported += 1
        except (KeyError, ValueError) as e:
            print(f"  ✗ {pair_name}: CSV 格式错误 ({e})，跳过此文件")
            conn.rollback()
            continue

        conn.commit()
        total_imported += imported
        print(f"  ✓ {pair_name}: 新增 {imported} 条")

    conn.close()
    print(f"  资金费率合计: 新增 {total_imported} 条")


def import_yield_rates(asset=None):
    """导入收益型资产利率 CSV 数据

    Args:
        asset: 资产名（bfusd/rwusd/ldusdt），为 None 时导入所有
    """
    if asset:
        items = [YIELD_DBS[asset]]
    else:
        items = list(YIELD_DBS.values())

    for cfg in items:
        csv_path = DB_DIR / f"{cfg['name']}_rate.csv"
        if not csv_path.exists():
            print(f"  文件不存在: {csv_path.name}")
            continue

        DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(cfg["db"]))
        ensure_table(conn, cfg["table"])

        existing = set(
            r[0] for r in conn.execute(
                f"SELECT date FROM {cfg['table']}"
            ).fetchall()
        )

        imported = 0
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    dt = row["date"]
                    if dt in existing:
                        continue
                    conn.execute(
                        f"INSERT INTO {cfg['table']} (date, apr) VALUES (?, ?)",
                        (dt, float(row["apr"])),
                    )
                    imported += 1
        except (KeyError, ValueError) as e:
            print(f"  ✗ {cfg['name']}: CSV 格式错误 ({e})，跳过此文件")
            conn.rollback()
            continue

        conn.commit()
        conn.close()
        print(f"  ✓ {cfg['name']}: 新增 {imported} 条")


def main():
    parser = argparse.ArgumentParser(description="从 CSV 导入数据到数据库（幂等，自动跳过重复）")
    parser.add_argument(
        "--pair", "-p",
        nargs="+",
        default=None,
        help="交易对（如 BTCUSDT ETHUSDT），默认全部",
    )
    parser.add_argument(
        "--yield-asset", "-y",
        nargs="+",
        default=None,
        choices=["bfusd", "rwusd", "ldusdt"],
        help="收益型资产（如 bfusd rwusd ldusdt），默认全部",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="列出 db/ 目录下可导入的 CSV 文件",
    )
    args = parser.parse_args()

    # --list 模式：列出可用文件
    if args.list:
        csv_files = sorted(DB_DIR.glob("*.csv"))
        if not csv_files:
            print("db/ 目录下没有 CSV 文件")
            return
        print("可导入的 CSV 文件：")
        for f in csv_files:
            size = f.stat().st_size
            print(f"  {f.name} ({size:,} bytes)")
        return

    # 导入资金费率（根据 CSV 文件是否存在自动判断，无需硬编码列表）
    print("导入资金费率数据 ...")
    if args.pair:
        for p in args.pair:
            p_upper = p.upper()
            csv_path = DB_DIR / f"funding_rate_{p_upper}.csv"
            if not csv_path.exists():
                print(f"  警告: 找不到 {csv_path.name}，跳过 {p_upper}")
                continue
            import_funding_rate(p_upper)
    else:
        import_funding_rate()

    # 导入收益型资产利率
    print("\n导入收益型资产利率数据 ...")
    if args.yield_asset:
        for a in args.yield_asset:
            import_yield_rates(a)
    else:
        import_yield_rates()

    print("\n导入完成。")


if __name__ == "__main__":
    main()

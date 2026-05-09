#!/usr/bin/env python3
"""将 files/ 目录下的资金费率日志文件导入 SQLite 数据库"""

import ast
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "funding_rate.db"
FILES_DIR = Path(__file__).resolve().parent / "files"


def create_table(conn):
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
    conn.commit()


def import_file(conn, log_file):
    count = 0
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = ast.literal_eval(line)
            except (ValueError, SyntaxError):
                continue
            conn.execute(
                "INSERT INTO funding_rate (pair, time, timestamp, funding_rate, price) VALUES (?, ?, ?, ?, ?)",
                (
                    record.get("交易对"),
                    record.get("时间"),
                    int(record.get("时间戳", 0)),
                    record.get("资金费率"),
                    record.get("价格"),
                ),
            )
            count += 1
    conn.commit()
    return count


def main():
    conn = sqlite3.connect(str(DB_PATH))
    create_table(conn)

    # 检查是否已有数据
    total = conn.execute("SELECT COUNT(*) FROM funding_rate").fetchone()[0]
    if total > 0:
        print(f"数据库中已有 {total} 条记录，跳过导入")
        conn.close()
        return

    log_files = sorted(FILES_DIR.glob("coin*.log"))
    if not log_files:
        print("错误: files/ 目录下没有找到 coin*.log 文件")
        conn.close()
        sys.exit(1)

    total_count = 0
    for f in log_files:
        n = import_file(conn, f)
        print(f"{f.name}: {n} 条")
        total_count += n

    print(f"\n导入完成，共 {total_count} 条记录 -> {DB_PATH}")
    conn.close()


if __name__ == "__main__":
    main()

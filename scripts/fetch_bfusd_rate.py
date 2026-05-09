#!/usr/bin/env python3
"""从币安 API 获取 BFUSD 年化利率并写入 SQLite 数据库

币安 BFUSD rateHistory API:
- GET /sapi/v1/bfusd/history/rateHistory
- 需要 timestamp + signature 认证（HMAC-SHA256）
- 按时间降序排列
- 默认返回 10 条，total 显示总记录数
- 每次请求 IP 权重 150

获取策略：用 endTime 逐批获取，每次用最后一条的 time-1 作为下一批的 endTime
"""

import hashlib
import hmac
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import configparser

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import get_proxies, ROOT, CONFIG_PATH

DB_PATH = ROOT / "db" / "bfusd.db"
proxies = None


def make_signed_url(base_url, params, secret):
    """手动构建带签名的完整 URL"""
    sorted_params = sorted((k, v) for k, v in params.items() if k != "signature")
    query_string = "&".join(f"{k}={v}" for k, v in sorted_params)
    signature = hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    return f"{base_url}?{query_string}&signature={signature}"


def create_table(conn):
    """创建数据库表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bfusd_rate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            apr REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON bfusd_rate(date)")
    conn.commit()


def fetch_all_data(conn, api_key, api_secret, latest_date):
    """用 endTime 逐批获取增量数据

    策略：
    - 从当前时间开始，每次获取 10 条
    - 用最后一条的 time-1 作为下一批的 endTime
    - 如果获取到的日期在数据库中已存在，说明已覆盖到数据库最新记录，停止
    - 直到返回空数据
    """
    api_base = "https://api.binance.com/sapi/v1/bfusd/history/rateHistory"
    total_inserted = 0
    batch_num = 0
    last_end_time = int(datetime.now().timestamp() * 1000)

    while True:
        batch_num += 1
        timestamp = str(int(time.time() * 1000))
        params = {
            "timestamp": timestamp,
            "endTime": str(last_end_time),
        }

        url = make_signed_url(api_base, params, api_secret)
        headers = {"X-MBX-APIKEY": api_key}

        try:
            resp = requests.get(url, headers=headers, proxies=proxies, timeout=30)
            if resp.status_code == 429:
                print(f"  第{batch_num}批: 限流，等待 5 秒...")
                sys.stdout.flush()
                time.sleep(5)
                continue
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            print(f"  第{batch_num}批: 请求异常: {e}")
            sys.stdout.flush()
            break

        rows = data.get("rows", [])
        if not rows:
            print(f"  第{batch_num}批: 无数据，获取完成")
            sys.stdout.flush()
            break

        newest_date_in_batch = datetime.fromtimestamp(rows[0]["time"] / 1000).strftime("%Y-%m-%d")

        # 如果这批里最新的日期不晚于数据库截止日，说明已覆盖
        if latest_date and newest_date_in_batch <= latest_date:
            print(f"  第{batch_num}批: 日期 {newest_date_in_batch} 已在数据库中(截止 {latest_date})，增量获取完成")
            sys.stdout.flush()
            break

        # 插入本批数据，跳过数据库中已有的日期
        inserted = 0
        for row in rows:
            ts = row["time"]
            date_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            apr = float(row["annualPercentageRate"])
            try:
                conn.execute(
                    "INSERT INTO bfusd_rate (date, apr) VALUES (?, ?)", (date_str, apr)
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass

        conn.commit()
        total_inserted += inserted

        # 用最后一条（最早的）的 time-1 作为下一批的 endTime
        last_time = rows[-1]["time"]
        last_end_time = last_time - 1

        oldest_date_in_batch = datetime.fromtimestamp(rows[-1]["time"] / 1000).strftime("%Y-%m-%d")
        print(f"  第{batch_num}批: {len(rows)} 条 ({newest_date_in_batch} ~ {oldest_date_in_batch}), 新增 {inserted} 条")
        sys.stdout.flush()

        time.sleep(3)  # 批次间延迟，避免限流

    return total_inserted


def main():
    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(str(CONFIG_PATH))

    global proxies
    proxies = get_proxies()
    if proxies:
        print(f"代理: {proxies['http']}")
        sys.stdout.flush()

    api_key = config.get("keys", "api_key", fallback="").strip()
    api_secret = config.get("keys", "api_secret", fallback="").strip()
    if not api_key or not api_secret:
        print("错误: config.ini 中 [keys] 段缺少 api_key 或 api_secret")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    create_table(conn)

    # 获取数据库最新日期
    row = conn.execute("SELECT MAX(date) FROM bfusd_rate").fetchone()
    latest_date = row[0] if row[0] else None

    if latest_date:
        print(f"数据库已有数据截止: {latest_date}")
        print("策略: 从当前时间开始逐批获取，遇到已有日期时停止")
    else:
        print("数据库为空，开始全量获取")

    total_inserted = fetch_all_data(conn, api_key, api_secret, latest_date)
    print(f"\n完成，共新增 {total_inserted} 条 -> {DB_PATH}")

    # 显示数据库状态
    total = conn.execute("SELECT COUNT(*) FROM bfusd_rate").fetchone()[0]
    earliest = conn.execute("SELECT MIN(date) FROM bfusd_rate").fetchone()[0]
    latest = conn.execute("SELECT MAX(date) FROM bfusd_rate").fetchone()[0]
    print(f"数据库: {total} 条记录 ({earliest} ~ {latest})")
    conn.close()


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"执行时间: {time.time() - t0:.2f}秒")

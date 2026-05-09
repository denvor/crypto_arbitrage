#!/usr/bin/env python3
"""从币安 API 获取资金费率并写入 SQLite 数据库"""

import configparser
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import os
import sys
import requests

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "db" / "funding_rate.db"
CONFIG_PATH = ROOT / "config.ini"
proxies = None


def get_proxies():
    """获取代理配置：环境变量 > config.ini"""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not proxy:
        try:
            cfg = configparser.ConfigParser()
            cfg.read(str(CONFIG_PATH))
            proxy = cfg.get("proxy", "url", fallback="").strip()
        except Exception:
            pass
    return {"http": proxy, "https": proxy} if proxy else None


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


def get_funding_rate(symbol, start_time, end_time, conn):
    """获取资金费率并写入数据库，跳过已存在的记录"""
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    params = {"symbol": symbol, "startTime": start_time, "endTime": end_time, "limit": 1000}

    try:
        resp = requests.get(url, params=params, timeout=10, proxies=proxies)
        resp.raise_for_status()
        funding_rates = resp.json()
    except Exception as e:
        print(f"  请求异常: {e}")
        return 0

    inserted = 0

    for rate in funding_rates:
        funding_ts = int(rate["fundingTime"])

        # 检查是否已存在
        existing = conn.execute(
            "SELECT 1 FROM funding_rate WHERE pair = ? AND timestamp = ?",
            (symbol, funding_ts),
        ).fetchone()
        if existing:
            continue

        # fundingRate API 返回的 markPrice 就是价格
        price_val = rate.get("markPrice") or "N/A"
        funding_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(funding_ts / 1000))

        conn.execute(
            "INSERT INTO funding_rate (pair, time, timestamp, funding_rate, price) VALUES (?, ?, ?, ?, ?)",
            (symbol, funding_time, funding_ts, rate["fundingRate"], price_val),
        )
        inserted += 1

    conn.commit()
    return inserted


def fetch_range(symbol, start_ts, end_ts, conn, db_pair=""):
    """分批获取资金费率，每批 1000 条，直到 end_ts
    symbol: API 请求用的交易对（小写，如 btcusdt）
    db_pair: 数据库存储用的交易对（大写，如 BTCUSDT）
    """
    total_inserted = 0
    cursor = start_ts
    batch = 0
    while cursor < end_ts:
        batch += 1
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        params = {"symbol": symbol, "startTime": cursor, "endTime": end_ts, "limit": 1000}

        try:
            resp = requests.get(url, params=params, timeout=10, proxies=proxies)
            resp.raise_for_status()
            rates = resp.json()
        except Exception as e:
            print(f"  请求异常: {e}")
            return 0

        if not rates:
            break

        inserted = 0
        price_url = "https://fapi.binance.com/fapi/v1/klines"

        for rate in rates:
            funding_ts = int(rate["fundingTime"])

            existing = conn.execute(
                "SELECT 1 FROM funding_rate WHERE pair = ? AND timestamp = ?",
                (db_pair, funding_ts),
            ).fetchone()
            if existing:
                continue

            # fundingRate API 返回的 markPrice 就是价格，为空时回退 klines
            price_val = rate.get("markPrice")
            if not price_val:
                price_resp = requests.get(price_url,
                    params={"symbol": symbol, "interval": "1m",
                            "startTime": funding_ts, "limit": 1},
                    proxies=proxies, timeout=10)
                if price_resp.status_code == 200:
                    kline = price_resp.json()
                    if kline:
                        price_val = kline[0][4]
            price_val = price_val or "N/A"
            funding_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(funding_ts / 1000))

            conn.execute(
                "INSERT INTO funding_rate (pair, time, timestamp, funding_rate, price) VALUES (?, ?, ?, ?, ?)",
                (db_pair, funding_time, funding_ts, rate["fundingRate"], price_val),
            )
            inserted += 1

        conn.commit()
        total_inserted += inserted

        # 下一批从当前最大时间戳开始
        cursor = int(rates[-1]["fundingTime"]) + 1

        if db_pair:
            print(f"    第{batch}批: {len(rates)} 条, 新增 {inserted} 条, 累计 {total_inserted} 条")
            sys.stdout.flush()

    return total_inserted


def main():
    config = configparser.ConfigParser()
    config.optionxform = str  # 保留 key 原始大小写
    config.read(str(CONFIG_PATH))

    global proxies
    proxies = get_proxies()

    conn = sqlite3.connect(str(DB_PATH))
    create_table(conn)

    # 获取昨天的日期
    yesterday = (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_ts = int(yesterday.timestamp() * 1000)

    # 找出每个交易对最新的 timestamp
    pairs = {}
    for name, _ in config.items("pairs"):
        pairs[name.lower()] = name

    pair_latest = {}
    for tbl in pairs:
        row = conn.execute(
            "SELECT MAX(timestamp) FROM funding_rate WHERE pair = ?", (tbl,)
        ).fetchone()
        pair_latest[tbl] = row[0] if row[0] else 0

    # 每个交易对独立从自己的最新记录开始
    MIN_TS = int(datetime(2019, 1, 1).timestamp() * 1000)

    total_inserted = 0
    for tbl, pair_name in pairs.items():
        latest = pair_latest[tbl]
        if latest >= end_ts:
            print(f"\n{pair_name}: 已是最新，跳过")
            continue

        pair_start = max(latest + 1, MIN_TS)
        start_date = datetime.fromtimestamp(pair_start / 1000)
        print(f"\n{pair_name} (从 {start_date.strftime('%Y-%m-%d')} 开始)")
        sys.stdout.flush()
        n = fetch_range(tbl, pair_start, end_ts, conn, db_pair=pair_name)
        print(f"  新增 {n} 条")
        total_inserted += n

    print(f"\n完成，共新增 {total_inserted} 条 -> {DB_PATH}")
    conn.close()


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"脚本执行时间: {time.time() - t0:.2f}秒")

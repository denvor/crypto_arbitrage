#!/usr/bin/env python3
"""补历史缺失数据（当 markPrice 为空时回退 klines API）"""

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests

proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
proxies = {"http": proxy, "https": proxy} if proxy else None

DB_PATH = Path(__file__).resolve().parent / "funding_rate.db"


def main():
    conn = sqlite3.connect(str(DB_PATH))

    # 手动指定需要补的数据范围
    # 格式: (交易对大写, API交易对小写, 起始日期, 结束日期)
    ranges = [
        ("ETHUSDT", "ethusdt", "2020-10-01", "2020-10-31"),
    ]

    total_inserted = 0

    for pair, symbol, start_date, end_date in ranges:
        print(f"\n{pair} ({start_date} ~ {end_date})")

        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)

        cursor = start_ts
        batch = 0
        while cursor < end_ts:
            batch += 1
            resp = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": symbol, "startTime": cursor, "endTime": end_ts, "limit": 1000},
                proxies=proxies, timeout=30,
            )
            resp.raise_for_status()
            rates = resp.json()

            if not rates:
                break

            inserted = 0
            price_url = "https://fapi.binance.com/fapi/v1/klines"

            for rate in rates:
                funding_ts = int(rate["fundingTime"])

                existing = conn.execute(
                    "SELECT 1 FROM funding_rate WHERE pair = ? AND timestamp = ?",
                    (pair, funding_ts),
                ).fetchone()
                if existing:
                    continue

                # 优先用 markPrice，为空时回退 klines
                price_val = rate.get("markPrice")
                if not price_val:
                    price_resp = requests.get(
                        price_url,
                        params={"symbol": symbol, "interval": "1m",
                                "startTime": funding_ts, "limit": 1},
                        proxies=proxies, timeout=10,
                    )
                    if price_resp.status_code == 200:
                        kline = price_resp.json()
                        if kline:
                            price_val = kline[0][4]
                price_val = price_val or "N/A"

                funding_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(funding_ts / 1000))

                conn.execute(
                    "INSERT INTO funding_rate (pair, time, timestamp, funding_rate, price) VALUES (?, ?, ?, ?, ?)",
                    (pair, funding_time, funding_ts, rate["fundingRate"], price_val),
                )
                inserted += 1

            conn.commit()
            total_inserted += inserted
            print(f"  第{batch}批: {len(rates)} 条, 新增 {inserted} 条")
            cursor = int(rates[-1]["fundingTime"]) + 1

        print(f"  {pair}: 新增 {inserted} 条")

    print(f"\n完成，共新增 {total_inserted} 条 -> {DB_PATH}")
    conn.close()


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"执行时间: {time.time() - t0:.2f}秒")

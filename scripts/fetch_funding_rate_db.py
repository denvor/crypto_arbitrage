#!/usr/bin/env python3
"""从币安 API 获取资金费率并写入 SQLite 数据库"""

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import get_proxies, ROOT, CONFIG_PATH

DB_PATH = ROOT / "db" / "funding_rate.db"
proxies = None
session = None



def get_session():
    """创建带代理的 Session"""
    global session
    if session is not None:
        return session
    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)
    return session


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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pair_ts ON funding_rate(pair, timestamp)")
    conn.commit()


def fetch_range(symbol, start_ts, end_ts, conn, db_pair="", total_pairs=1, pair_index=1):
    """分批获取资金费率，每批 1000 条，直到 end_ts"""
    total_inserted = 0
    cursor = start_ts
    batch = 0
    total_ms = max(1, end_ts - start_ts)
    while cursor < end_ts:
        batch += 1
        elapsed_ms = cursor - start_ts
        pct = min(99.9, elapsed_ms / total_ms * 100)
        cursor_date = datetime.fromtimestamp(cursor / 1000).strftime("%Y-%m-%d")

        params = {"symbol": symbol, "startTime": cursor, "endTime": end_ts, "limit": 1000}
        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"  [{now_str}] [{pair_index}/{total_pairs}] {db_pair} 第{batch}批 → {cursor_date} (已完成{pct:.0f}%)")
        sys.stdout.flush()

        s = get_session()
        rates = None
        t_req = time.time()
        for attempt in range(3):
            try:
                print(f"    请求 Binance API... ", end="")
                sys.stdout.flush()
                resp = s.get("https://fapi.binance.com/fapi/v1/fundingRate",
                             params=params, timeout=(10, 20))
                resp.raise_for_status()
                rates = resp.json()
                print(f"返回 {len(rates)} 条  ({time.time()-t_req:.1f}s)")
                sys.stdout.flush()
                break
            except requests.exceptions.Timeout:
                print(f"超时 (第{attempt+1}次/3) ({time.time()-t_req:.1f}s)")
            except requests.exceptions.ConnectionError as e:
                print(f"连接失败 (第{attempt+1}次/3) ({time.time()-t_req:.1f}s): {e}")
            except requests.exceptions.HTTPError as e:
                if resp.status_code == 429:
                    print(f"限流 (第{attempt+1}次/3), 等待 5 秒 ({time.time()-t_req:.1f}s)")
                    time.sleep(5)
                    continue
                print(f"HTTP {resp.status_code}: {e} ({time.time()-t_req:.1f}s)")
                break
            except Exception as e:
                print(f"请求异常: {e} ({time.time()-t_req:.1f}s)")
                break
            if attempt < 2:
                wait = (attempt + 1) * 2
                print(f"    等待 {wait} 秒后重试...")
                time.sleep(wait)

        if rates is None:
            print(f"    重试耗尽，跳过本批")
            return total_inserted

        if not rates:
            print(f"    无更多数据")
            break

        # ---- 去重检查 ----
        t0 = time.time()
        # 一次性加载该交易对全部已有 timestamp
        print(f"    加载已有数据... ", end="")
        sys.stdout.flush()
        existing_set = set(
            row[0] for row in conn.execute(
                "SELECT timestamp FROM funding_rate WHERE pair = ?", (db_pair,)
            ).fetchall()
        )
        print(f"{len(existing_set)} 个 ({time.time()-t0:.2f}s)")

        t0 = time.time()
        price_fallback_count = 0
        to_insert = []
        for rate in rates:
            funding_ts = int(rate["fundingTime"])
            if funding_ts in existing_set:
                continue

            price_val = rate.get("markPrice") or "N/A"
            if price_val == "N/A":
                price_fallback_count += 1
            funding_time = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(funding_ts / 1000))
            to_insert.append((db_pair, funding_time, funding_ts,
                              rate["fundingRate"], price_val))

        t_check = time.time() - t0

        # ---- 入库 ----
        t0 = time.time()
        for row in to_insert:
            conn.execute(
                "INSERT INTO funding_rate (pair, time, timestamp, "
                "funding_rate, price) VALUES (?, ?, ?, ?, ?)", row)
        conn.commit()
        t_insert = time.time() - t0

        total_inserted += len(to_insert)
        cursor = int(rates[-1]["fundingTime"]) + 1

        if db_pair:
            current_date = datetime.fromtimestamp(
                int(rates[-1]["fundingTime"]) / 1000).strftime("%Y-%m-%d")
            print(f"    ↑ 新增{len(to_insert)}条 累计{total_inserted}条 → {current_date}  "
                  f"(去重{t_check:.2f}s 入库{t_insert:.2f}s"
                  + (f" 价格回退{price_fallback_count}次)" if price_fallback_count else ")"))
            sys.stdout.flush()

    return total_inserted


def main():
    import configparser
    config = configparser.ConfigParser()
    config.optionxform = str  # 保留 key 原始大小写
    config.read(str(CONFIG_PATH))

    global proxies
    proxies = get_proxies()
    if proxies:
        print(f"代理: {proxies['http']}")
        sys.stdout.flush()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    create_table(conn)

    # 获取昨天的日期
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    # fundingTime 有毫秒偏移，加 10 秒缓冲
    end_ts = int(today.timestamp() * 1000) - 1 + 10000

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

    total_pairs = len([t for t in pairs if pair_latest[t] < end_ts])
    pair_counter = 0
    total_inserted = 0
    for tbl, pair_name in pairs.items():
        latest = pair_latest[tbl]
        if latest >= end_ts:
            print(f"  [{pair_name}] 已是最新，跳过")
            continue

        pair_counter += 1
        pair_start = max(latest + 1, MIN_TS)
        start_date = datetime.fromtimestamp(pair_start / 1000)
        end_date_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"\n[{pair_counter}/{total_pairs}] {pair_name}  {start_date.strftime('%Y-%m-%d')} → {end_date_str}")
        sys.stdout.flush()
        n = fetch_range(tbl, pair_start, end_ts, conn, db_pair=pair_name,
                        total_pairs=total_pairs, pair_index=pair_counter)
        print(f"  ✓ {pair_name} 完成，新增 {n} 条")
        total_inserted += n

    print(f"\n完成，共新增 {total_inserted} 条 -> {DB_PATH}")
    conn.close()


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"脚本执行时间: {time.time() - t0:.2f}秒")

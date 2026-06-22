#!/usr/bin/env python3
"""从币安 API 获取 LDUSDT 年化利率并写入 SQLite 数据库

币安 LDUSDT 的 APR 挂钩 Simple Earn USDT 活期产品利率:

Step 1: 通过 GET /sapi/v1/simple-earn/flexible/list 查询 USDT 活期产品 productId
Step 2: 用 productId 调用 GET /sapi/v1/simple-earn/flexible/history/rateHistory 获取历史利率

需要 timestamp + signature 认证（HMAC-SHA256）
每次请求 IP 权重 150
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

DB_PATH = ROOT / "db" / "ldusdt.db"
proxies = None
session = None
API_BASE = "https://api.binance.com"


def get_session():
    """创建带代理的 Session"""
    global session
    if session is not None:
        return session
    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)
    return session


def make_signed_url(base_url, params, secret):
    """手动构建带签名的完整 URL"""
    sorted_params = sorted((k, v) for k, v in params.items() if k != "signature")
    query_string = "&".join(f"{k}={v}" for k, v in sorted_params)
    signature = hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    return f"{base_url}?{query_string}&signature={signature}"


def create_table(conn):
    """创建数据库表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ldusdt_rate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            apr REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON ldusdt_rate(date)")
    conn.commit()


def safe_signed_get(url, headers):
    """带重试的签名 GET 请求，返回 response json 或 None"""
    s = get_session()
    for attempt in range(3):
        try:
            print(f"    请求 Binance API... ", end="")
            sys.stdout.flush()
            resp = s.get(url, headers=headers, timeout=(10, 20))
            resp.raise_for_status()
            print("完成")
            return resp.json()
        except requests.exceptions.Timeout:
            print(f"超时 (第{attempt+1}次/3)")
        except requests.exceptions.ConnectionError as e:
            print(f"连接失败 (第{attempt+1}次/3): {e}")
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429:
                print(f"限流 (第{attempt+1}次/3), 等待 5 秒")
                time.sleep(5)
                continue
            print(f"HTTP {resp.status_code}: {e}")
            return None
        except Exception as e:
            print(f"请求异常: {e}")
            return None
        if attempt < 2:
            wait = (attempt + 1) * 2
            print(f"    等待 {wait} 秒后重试...")
            time.sleep(wait)
    print("    重试耗尽，跳过本批")
    return None


def discover_product_id(api_key, api_secret):
    """查询 Simple Earn 活期产品列表，找到 USDT 的 productId

    GET /sapi/v1/simple-earn/flexible/list
    返回 productId (str) 或 None
    """
    print("  查询 Simple Earn USDT 活期产品 productId...")
    sys.stdout.flush()

    timestamp = str(int(time.time() * 1000))
    params = {
        "timestamp": timestamp,
        "size": "100",
    }

    url = make_signed_url(f"{API_BASE}/sapi/v1/simple-earn/flexible/list", params, api_secret)
    headers = {"X-MBX-APIKEY": api_key}

    data = safe_signed_get(url, headers)
    if data is None:
        print("  ⚠ 获取活期产品列表失败")
        return None

    rows = data.get("rows", [])
    for row in rows:
        if row.get("asset") == "USDT":
            pid = row.get("productId")
            apr = row.get("annualPercentageRate", "N/A")
            print(f"  找到 USDT 活期产品: productId={pid}, currentAPR={apr}")
            sys.stdout.flush()
            return pid

    print("  ⚠ 未找到 USDT 活期产品")
    return None


def fetch_all_data(conn, api_key, api_secret, product_id, latest_date, launch_ts=0):
    """用 productId 和 endTime 逐批获取利率历史

    策略同 fetch_bfusd_rate.py:
    - 从当前时间开始，每次获取 100 条
    - 用最后一条的 time-1 作为下一批的 endTime
    - 遇到数据库中已有的日期时停止
    - launch_ts: 上线日期时间戳（ms），endTime 低于此值时停止
    """
    api_base = f"{API_BASE}/sapi/v1/simple-earn/flexible/history/rateHistory"
    total_inserted = 0
    batch_num = 0
    last_end_time = int(datetime.now().timestamp() * 1000)
    launch_date_str = datetime.fromtimestamp(launch_ts / 1000).strftime("%Y-%m-%d") if launch_ts > 0 else ""

    while True:
        # 检查 endTime 是否已低于上线日期
        if launch_ts > 0 and last_end_time < launch_ts:
            print(f"  [{time.strftime('%H:%M:%S')}] endTime 已低于上线日期 {launch_date_str}，获取完成")
            sys.stdout.flush()
            break

        batch_num += 1
        now_str = time.strftime("%H:%M:%S")
        print(f"  [{now_str}] 第{batch_num}批 {total_inserted}条已获取 → 请求中...")
        sys.stdout.flush()

        timestamp = str(int(time.time() * 1000))
        params = {
            "productId": product_id,
            "timestamp": timestamp,
            "endTime": str(last_end_time),
            "size": "100",
        }

        url = make_signed_url(api_base, params, api_secret)
        headers = {"X-MBX-APIKEY": api_key}

        data = safe_signed_get(url, headers)
        if data is None:
            print(f"  ⚠ 第{batch_num}批获取失败，停止")
            break

        rows = data.get("rows", [])
        if not rows:
            print(f"  [{now_str}] 第{batch_num}批: 无更多数据，获取完成")
            sys.stdout.flush()
            break

        newest_date_in_batch = datetime.fromtimestamp(rows[0]["time"] / 1000).strftime("%Y-%m-%d")

        # 如果这批里最新的日期不晚于数据库截止日，说明已覆盖
        if latest_date and newest_date_in_batch <= latest_date:
            print(f"  [{now_str}] 第{batch_num}批: 日期 {newest_date_in_batch} 已在数据库中(截止 {latest_date})，增量获取完成")
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
                    "INSERT INTO ldusdt_rate (date, apr) VALUES (?, ?)", (date_str, apr)
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
        print(f"    ↑ 新增{inserted}条 累计{total_inserted}条 ({newest_date_in_batch}~{oldest_date_in_batch})")
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

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    create_table(conn)

    # Step 1: 发现 productId
    print("Step 1: 查询 LDUSDT (Simple Earn USDT) 产品信息")
    product_id = discover_product_id(api_key, api_secret)
    if not product_id:
        print("错误: 无法获取 LDUSDT 的 productId")
        print("提示: 请确认 API Key 有 Simple Earn 权限")
        conn.close()
        sys.exit(1)

    # Step 2: 获取历史利率
    print(f"\nStep 2: 使用 productId={product_id} 获取利率历史")

    # 读取 LDUSDT 上线日期作为硬限制
    ldusdt_start = config.get("ldusdt", "start_date", fallback="")
    launch_ts = 0
    if ldusdt_start:
        launch_ts = int(datetime.strptime(ldusdt_start, "%Y-%m-%d").timestamp() * 1000)
        print(f"LDUSDT 上线日期: {ldusdt_start}，endTime 不低于此值")

    # 获取数据库最新日期
    row = conn.execute("SELECT MAX(date) FROM ldusdt_rate").fetchone()
    latest_date = row[0] if row[0] else None

    if latest_date:
        print(f"数据库已有数据截止: {latest_date}")
        print("策略: 从当前时间开始逐批获取，遇到已有日期时停止")
    else:
        print("数据库为空，开始全量获取")

    total_inserted = fetch_all_data(conn, api_key, api_secret, product_id, latest_date, launch_ts=launch_ts)
    print(f"\n完成，共新增 {total_inserted} 条 -> {DB_PATH}")

    # 显示数据库状态
    total = conn.execute("SELECT COUNT(*) FROM ldusdt_rate").fetchone()[0]
    earliest = conn.execute("SELECT MIN(date) FROM ldusdt_rate").fetchone()[0]
    latest = conn.execute("SELECT MAX(date) FROM ldusdt_rate").fetchone()[0]
    print(f"数据库: {total} 条记录 ({earliest} ~ {latest})")
    conn.close()


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"执行时间: {time.time() - t0:.2f}秒")

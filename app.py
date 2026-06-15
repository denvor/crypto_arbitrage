#!/usr/bin/env python3
"""crypto Arbitrage WebUI -- 数据维护页面

启动: python app.py
访问: http://127.0.0.1:5000/maintenance
"""

import hashlib
import hmac
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from flask import Flask, jsonify, render_template, request, url_for

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from utils import (
    BFUSD_DB, FUNDING_DB,
    create_job, get_job, update_job,
    get_proxies, load_config, get_pairs,
    get_pair_stats, get_bfusd_stats,
)

# 导入回测核心函数
from backtest import run_backtest, load_log_data, load_bfusd_rates

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(PROJECT_DIR, "static"), static_url_path="/static")


# ---- 后台更新函数 ----

def run_funding_rate_update(job):
    """在后台线程中执行资金费率增量更新"""
    try:
        config = load_config()
        proxies = get_proxies()
        pairs = get_pairs(config)

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

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        # fundingTime 有毫秒偏移（如整点的 timestamp + 几毫秒）
        # 拉到今天 00:00 之前（即昨天 23:59:59.999），加 10 秒缓冲
        end_ts = int(today.timestamp() * 1000) - 1 + 10000
        MIN_TS = int(datetime(2019, 1, 1).timestamp() * 1000)

        if job["pair"]:
            pair_key = job["pair"].lower()
            if pair_key not in pairs:
                raise ValueError(f"未知交易对: {job['pair']}")
            pairs = {pair_key: pairs[pair_key]}

        total_inserted = 0
        for tbl, pair_name in pairs.items():
            row = conn.execute(
                "SELECT MAX(timestamp) FROM funding_rate WHERE pair = ?", (tbl,)
            ).fetchone()
            latest = row[0] if row[0] else 0

            if latest >= end_ts:
                update_job(job["job_id"], message=f"{pair_name}: 已是最新")
                continue

            pair_start = max(latest + 1, MIN_TS)
            cursor = pair_start
            batch = 0
            pair_inserted = 0
            sess = requests.Session()
            if proxies:
                sess.proxies.update(proxies)

            while cursor < end_ts:
                batch += 1
                try:
                    resp = sess.get(
                        "https://fapi.binance.com/fapi/v1/fundingRate",
                        params={"symbol": tbl, "startTime": cursor,
                                "endTime": end_ts, "limit": 1000},
                        timeout=(10, 20),
                    )
                    resp.raise_for_status()
                    rates = resp.json()
                except Exception as e:
                    update_job(job["job_id"],
                               message=f"{pair_name}: 第{batch}批请求失败: {e}")
                    break

                if not rates:
                    break

                # 一次性加载已有 timestamp 到 set
                existing_set = set(
                    r[0] for r in conn.execute(
                        "SELECT timestamp FROM funding_rate WHERE pair = ?",
                        (pair_name,),
                    ).fetchall()
                )

                inserted = 0
                for rate in rates:
                    funding_ts = int(rate["fundingTime"])
                    if funding_ts in existing_set:
                        continue

                    price_val = rate.get("markPrice") or "N/A"
                    funding_time = time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(funding_ts / 1000)
                    )
                    conn.execute(
                        "INSERT INTO funding_rate (pair, time, timestamp, "
                        "funding_rate, price) VALUES (?, ?, ?, ?, ?)",
                        (pair_name, funding_time, funding_ts,
                         rate["fundingRate"], price_val),
                    )
                    inserted += 1

                conn.commit()
                pair_inserted += inserted
                total_inserted += inserted
                cursor = int(rates[-1]["fundingTime"]) + 1
                update_job(job["job_id"],
                           message=f"{pair_name}: 第{batch}批, 新增{inserted}条, 累计{pair_inserted}条")

            update_job(job["job_id"],
                       message=f"{pair_name}: 完成, 新增 {pair_inserted} 条")

        update_job(job["job_id"], new_records=total_inserted,
                   message=f"完成, 共新增 {total_inserted} 条")
        conn.close()

    except Exception as e:
        update_job(job["job_id"], error=str(e),
                   message=f"获取失败: {e}")
    finally:
        update_job(job["job_id"], status="done")


def run_bfusd_update(job):
    """在后台线程中执行 BFUSD 利率增量更新"""
    try:
        config = load_config()
        proxies = get_proxies()
        api_key = config.get("keys", "api_key", fallback="").strip()
        api_secret = config.get("keys", "api_secret", fallback="").strip()

        if not api_key or not api_secret:
            raise ValueError("config.ini 中 [keys] 段缺少 api_key 或 api_secret")
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

        row = conn.execute("SELECT MAX(date) FROM bfusd_rate").fetchone()
        latest_date = row[0] if row[0] else None

        api_base = "https://api.binance.com/sapi/v1/bfusd/history/rateHistory"
        total_inserted = 0
        batch_num = 0
        last_end_time = int(datetime.now().timestamp() * 1000)
        sess = requests.Session()
        if proxies:
            sess.proxies.update(proxies)

        def make_signed_url(base_url, params, secret):
            sorted_params = sorted((k, v) for k, v in params.items()
                                   if k != "signature")
            query_string = "&".join(f"{k}={v}" for k, v in sorted_params)
            signature = hmac.new(
                secret.encode(), query_string.encode(), hashlib.sha256
            ).hexdigest()
            return f"{base_url}?{query_string}&signature={signature}"

        while True:
            batch_num += 1
            timestamp = str(int(time.time() * 1000))
            params = {"timestamp": timestamp, "endTime": str(last_end_time)}
            url = make_signed_url(api_base, params, api_secret)
            headers = {"X-MBX-APIKEY": api_key}

            update_job(job["job_id"],
                       message=f"BFUSD: 第{batch_num}批...")

            try:
                resp = sess.get(url, headers=headers, timeout=(10, 20))
                if resp.status_code == 429:
                    time.sleep(5)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                raise RuntimeError(f"请求异常: {e}")

            rows = data.get("rows", [])
            if not rows:
                update_job(job["job_id"],
                           message=f"BFUSD: 获取完成, 共新增 {total_inserted} 条")
                break

            newest_date = datetime.fromtimestamp(
                rows[0]["time"] / 1000
            ).strftime("%Y-%m-%d")
            if latest_date and newest_date <= latest_date:
                update_job(job["job_id"],
                           message=(
                               f"BFUSD: 日期 {newest_date} 已在数据库中, "
                               f"增量获取完成"
                           ))
                break

            inserted = 0
            for row in rows:
                date_str = datetime.fromtimestamp(
                    row["time"] / 1000
                ).strftime("%Y-%m-%d")
                apr = float(row["annualPercentageRate"])
                try:
                    conn.execute(
                        "INSERT INTO bfusd_rate (date, apr) VALUES (?, ?)",
                        (date_str, apr),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass

            conn.commit()
            total_inserted += inserted
            last_end_time = rows[-1]["time"] - 1
            time.sleep(3)

        update_job(job["job_id"], new_records=total_inserted)
        conn.close()

    except Exception as e:
        update_job(job["job_id"], error=str(e),
                   message=f"BFUSD 获取失败: {e}")
    finally:
        update_job(job["job_id"], status="done")


# ---- Flask 路由 ----

@app.route("/static/manifest.json")
def manifest():
    """Serve Web App Manifest for PWA"""
    from flask import jsonify, Response
    manifest_data = {
        "name": "crypto Arbitrage — 资金费率套利回测",
        "short_name": "Arbitrage",
        "description": "币安合约资金费率套利回测工具",
        "start_url": "/backtest",
        "display": "standalone",
        "background_color": "#0f1117",
        "theme_color": "#6366f1",
        "orientation": "portrait-primary",
        "categories": ["finance", "productivity"],
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    resp = jsonify(manifest_data)
    resp.mimetype = "application/manifest+json"
    return resp


@app.route("/")
def index():
    """首页 — 导航到数据维护和回测页面"""
    return render_template("index.html")


@app.route("/maintenance")
def maintenance():
    """数据维护页面"""
    config = load_config()
    pairs = get_pairs(config)

    entries = []

    # 资金费率条目
    FUNDING_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(FUNDING_DB))
    stats = get_pair_stats(conn)
    conn.close()

    stat_map = {s["pair"]: s for s in stats}
    for key, name in pairs.items():
        s = stat_map.get(name, {})
        entries.append({
            "key": f"fr_{name}",
            "source": "资金费率",
            "type": "funding_rate",
            "pair": name,
            "min_date": s.get("min_date"),
            "max_date": s.get("max_date"),
            "count": s.get("count", 0),
        })

    # BFUSD 条目
    BFUSD_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(BFUSD_DB))
    bfusd = get_bfusd_stats(conn)
    conn.close()

    entries.append({
        "key": "bfusd",
        "source": "BFUSD 利率",
        "type": "bfusd",
        "pair": "BFUSD",
        "min_date": bfusd["min_date"],
        "max_date": bfusd["max_date"],
        "count": bfusd["count"],
    })

    return render_template("maintenance.html", entries=entries)


@app.route("/api/fetch/start", methods=["POST"])
def api_fetch_start():
    """启动后台获取任务"""
    data = request.get_json()
    job_type = data.get("type")
    pair = data.get("pair")

    if job_type not in ("funding_rate", "bfusd"):
        return jsonify({"error": f"未知类型: {job_type}"}), 400

    job = create_job(job_type, pair)

    target = (run_funding_rate_update
              if job_type == "funding_rate" else run_bfusd_update)
    t = threading.Thread(target=target, args=(job,), daemon=True)
    t.start()

    return jsonify({"job_id": job["job_id"], "status": "started"})


@app.route("/api/fetch/status/<job_id>")
def api_fetch_status(job_id):
    """获取获取任务状态"""
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/backtest")
def backtest():
    """回测页面"""
    config = load_config()
    pairs = get_pairs(config)

    # 获取各交易对数据范围
    FUNDING_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(FUNDING_DB))
    stats = get_pair_stats(conn)
    conn.close()

    pair_ranges = {}
    for s in stats:
        pair_ranges[s["pair"]] = {
            "min": s["min_date"][:10],
            "max": s["max_date"][:10],
            "count": s["count"],
        }
    # 为暂无数据的交易对填充默认值，避免模板渲染报错
    for key, name in pairs.items():
        if name not in pair_ranges:
            pair_ranges[name] = {"min": "--", "max": "--", "count": 0}

    return render_template("backtest.html", pairs=pairs, pair_ranges=pair_ranges)


@app.route("/api/backtest/run", methods=["POST"])
def api_backtest_run():
    """执行回测并返回结果"""
    data = request.get_json()

    # 解析参数
    pairs_input = data.get("pairs", [])
    start_date = data.get("start")
    end_date = data.get("end")
    try:
        capital = float(data.get("capital", 10000))
        leverage = float(data.get("leverage", 1))
        futures_fee = float(data.get("futures_fee", 0.0004))
        spot_fee = float(data.get("spot_fee", 0.0004))
        slippage = float(data.get("slippage", 0.0001))
    except (ValueError, TypeError):
        return jsonify({"error": "资金、杠杆、费率参数必须为数值"}), 400

    if capital <= 0:
        return jsonify({"error": "总资金必须为正数"}), 400
    if leverage <= 0:
        return jsonify({"error": "杠杆倍数必须为正数"}), 400

    with_bfusd = data.get("with_bfusd", False)

    if not start_date or not end_date:
        return jsonify({"error": "请指定回测日期范围"}), 400

    # 加载配置
    config = load_config()
    pairs = get_pairs(config)
    fees = {
        "futures_fee": futures_fee,
        "spot_fee": spot_fee,
        "slippage": slippage,
    }

    # 验证交易对
    if pairs_input:
        selected = []
        for p in pairs_input:
            pk = p.lower()
            if pk in pairs:
                selected.append(pk)
        pairs = {k: {"name": pairs[k], "time": config.get("pairs", pairs[k], fallback="2019-09-10")} for k in selected}

    if not pairs:
        return jsonify({"error": "没有有效的交易对"}), 400

    # 加载 BFUSD 数据
    bfusd_rates = {}
    if with_bfusd:
        try:
            bfusd_rates = load_bfusd_rates(start_date, end_date)
        except Exception:
            pass

    # 运行回测
    results = {}
    for pair_key, pair_info in pairs.items():
        pair_name = pair_info["name"]
        launch_date = pair_info["time"]
        actual_start = max(start_date, launch_date)
        if actual_start > end_date:
            continue

        records = load_log_data(pair_name, actual_start, end_date)
        if not records:
            continue

        try:
            stats = run_backtest(records, capital, leverage, fees,
                                 with_bfusd=with_bfusd, bfusd_rates=bfusd_rates)
        except ValueError as e:
            return jsonify({"error": f"回测数据错误 ({pair_name}): {e}"}), 400
        stats["_name"] = pair_name
        stats["_pair_key"] = pair_key
        stats["_launch_date"] = launch_date
        results[pair_key] = stats

    return jsonify({"results": results})


if __name__ == "__main__":
    print("启动 crypto Arbitrage WebUI: http://127.0.0.1:5000/maintenance")
    app.run(host="127.0.0.1", port=5000, debug=False)

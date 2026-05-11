#!/usr/bin/env python3
"""资金费率套利回测程序

策略: 做空合约 + 做多现货（等值持仓），每次正资金费率获得收益，
负资金费率支付成本。收益 = 累计资金费率收益 - 开平仓手续费 - 开平仓滑点成本。
"""

import argparse
import configparser
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
CONFIG_FILE = ROOT / "config.ini"
DB_PATH = ROOT / "db" / "funding_rate.db"
BFUSD_DB_PATH = ROOT / "db" / "bfusd.db"


def load_config():
    """加载 config.ini 获取交易对配置和回测参数

    [pairs] 段格式: 交易对 = 上线日期
    [backtest] 段: futures_fee, spot_fee, slippage
    返回 (pairs_dict, fees_dict)
    """
    config = configparser.ConfigParser()
    config.optionxform = str  # 保留 key 原始大小写
    config.read(str(CONFIG_FILE))
    pairs = {}
    for name, time in config.items("pairs"):
        tbl = name.lower()
        pairs[tbl] = {"name": name, "tbl": tbl, "time": time}
    fees = {
        "futures_fee": float(config.get("backtest", "futures_fee", fallback="0.0004")),
        "spot_fee": float(config.get("backtest", "spot_fee", fallback="0.0004")),
        "slippage": float(config.get("backtest", "slippage", fallback="0.0001")),
    }
    return pairs, fees


def load_log_data(pair_name, start_date, end_date):
    """从数据库加载指定交易对、指定时间段内的资金费率数据"""
    conn = sqlite3.connect(str(DB_PATH))
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)

    rows = conn.execute(
        "SELECT time, timestamp, funding_rate, price FROM funding_rate "
        "WHERE pair = ? AND timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp",
        (pair_name, start_ts, end_ts),
    ).fetchall()
    conn.close()

    records = []
    for row in rows:
        records.append({
            "时间": row[0],
            "时间戳": row[1],
            "资金费率": row[2],
            "价格": row[3],
        })
    return records


def load_bfusd_rates(start_date, end_date):
    """从 BFUSD 数据库加载时间段内的每日年化利率

    返回 dict: {date_str: apr}，date_str 格式为 YYYY-MM-DD
    """
    conn = sqlite3.connect(str(BFUSD_DB_PATH))
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)

    rows = conn.execute(
        "SELECT date, apr FROM bfusd_rate "
        "WHERE date >= ? AND date <= ? "
        "ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    conn.close()

    return {row[0]: row[1] for row in rows}


def run_backtest(records, capital, leverage, fees, with_bfusd=False, bfusd_rates=None):
    """运行回测，返回统计结果和每笔明细

    资金分配模型 (套利: 合约仓位 = 现货价值):
    - 现货投入 = 合约仓位价值 = P
    - 合约保证金 = P / leverage
    - 总资金 = P + P/leverage = capital
    - 所以 P = capital * leverage / (leverage + 1)

    仓位价值随价格变化:
    - 合约数量 = 初始仓位价值 / 初始价格（固定）
    - 每次费率时仓位价值 = 合约数量 × 当时价格（随价格波动）
    - 资金费率收益 = 费率 × 当时仓位价值
    """
    futures_fee_rate = fees["futures_fee"]
    spot_fee_rate = fees["spot_fee"]
    slippage_rate = fees["slippage"]

    # 资金分配（基于初始价格）
    initial_position = capital * leverage / (leverage + 1)
    spot_cost = initial_position
    margin = initial_position / leverage

    funding_profit = 0.0
    bfusd_profit = 0.0
    bfusd_calculated = set()  # 记录已计算 BFUSD 的日期，避免同一天重复计算
    positive_count = 0
    negative_count = 0
    max_profit = float("-inf")
    max_loss = float("inf")
    detail = []

    # 合约数量固定（用第一笔价格计算）
    num_contracts = initial_position / float(records[0]["价格"])

    # 手续费和滑点只在开仓和平仓时各收一次
    total_fee_cost = -2 * (futures_fee_rate + spot_fee_rate) * initial_position
    total_slippage_cost = -2 * slippage_rate * initial_position

    for i, rec in enumerate(records):
        rate = float(rec["资金费率"])
        price_str = rec.get("价格", "").strip()
        price = float(price_str) if price_str else 0.0
        funding_time = rec["时间"]
        ts = int(rec["时间戳"])

        # 仓位价值随价格变化
        notional = num_contracts * price
        funding_pnl = rate * notional

        # BFUSD 年化收益（每日按保证金计算，同一天只算一次）
        if with_bfusd and bfusd_rates:
            date_str = funding_time[:10]
            if date_str not in bfusd_calculated:
                bfusd_calculated.add(date_str)
                apr = bfusd_rates.get(date_str, 0)
                daily_rate = apr / 365
                bfusd_profit += daily_rate * margin

        funding_profit += funding_pnl

        if rate > 0:
            positive_count += 1
        else:
            negative_count += 1

        if funding_pnl > max_profit:
            max_profit = funding_pnl
        if funding_pnl < max_loss:
            max_loss = funding_pnl

        # 手续费和滑点在首尾各收一次，中间行不重复显示
        line_fee = total_fee_cost if i == 0 else 0.0
        line_slippage = total_slippage_cost if i == 0 else 0.0
        line_net = funding_pnl + line_fee + line_slippage
        running = funding_profit + line_fee + line_slippage

        detail.append({
            "time": funding_time,
            "rate": rate,
            "price": price,
            "funding_pnl": funding_pnl,
            "fee_pnl": line_fee,
            "slippage_pnl": line_slippage,
            "net_pnl": line_net,
            "cumulative": running,
        })

    net_profit = funding_profit + bfusd_profit + total_fee_cost + total_slippage_cost
    net_pct = (net_profit / capital) * 100 if capital else 0
    funding_pct = (funding_profit / capital) * 100 if capital else 0
    bfusd_pct = (bfusd_profit / capital) * 100 if capital else 0

    # 计算持有天数用于年化
    if len(records) >= 2:
        days = (int(records[-1]["时间戳"]) - int(records[0]["时间戳"])) / (1000 * 60 * 60 * 24)
    else:
        days = 1
    annualized = (net_pct / days * 365) if days > 0 else 0

    return {
        "capital": capital,
        "spot_cost": spot_cost,
        "margin": margin,
        "initial_position": initial_position,
        "leverage": leverage,
        "funding_profit": funding_profit,
        "funding_pct": funding_pct,
        "bfusd_profit": bfusd_profit,
        "bfusd_pct": bfusd_pct,
        "fee_cost": total_fee_cost,
        "fee_pct": (total_fee_cost / capital * 100) if capital else 0,
        "slippage_cost": total_slippage_cost,
        "slippage_pct": (total_slippage_cost / capital * 100) if capital else 0,
        "net_profit": net_profit,
        "net_pct": net_pct,
        "annualized": annualized,
        "total_count": len(records),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "max_profit": max_profit if max_profit != float("-inf") else 0,
        "max_loss": max_loss if max_loss != float("inf") else 0,
        "days": days,
        "detail": detail,
    }


def format_result(pair_key, pair_info, stats, with_bfusd=False):
    """格式化输出单个交易对的回测结果"""
    lines = []
    lines.append(f"交易对: {pair_info['name']}")
    lines.append(f"期间: {stats['detail'][0]['time'][:10]} ~ {stats['detail'][-1]['time'][:10]}" if stats["detail"] else "期间: N/A")
    lines.append(f"总资金: {stats['capital']:,.2f} USDT")
    spot_pct = stats['spot_cost'] / stats['capital'] * 100 if stats['capital'] else 0
    margin_pct = stats['margin'] / stats['capital'] * 100 if stats['capital'] else 0
    lines.append(f"  现货: {stats['spot_cost']:,.2f} USDT ({spot_pct:.1f}%)")
    lines.append(f"  合约保证金: {stats['margin']:,.2f} USDT ({margin_pct:.1f}%)")
    lines.append(f"合约杠杆: {stats['leverage']}x | 初始仓位: {stats['initial_position']:,.2f} USDT")
    lines.append(f"持有天数: {stats['days']:.1f} 天")
    lines.append(f"注: 实际仓位价值随价格波动")
    lines.append("")

    lines.append(f"资金费率收益:   {stats['funding_profit']:+,.2f} USDT  ({stats['funding_pct']:+.2f}%)")
    if with_bfusd and stats['bfusd_profit'] != 0:
        lines.append(f"BFUSD 年化收益: {stats['bfusd_profit']:+,.2f} USDT  ({stats['bfusd_pct']:+.2f}%)")
    lines.append(f"手续费:         {stats['fee_cost']:+,.2f} USDT  ({stats['fee_pct']:+.2f}%)")
    lines.append(f"滑点成本:       {stats['slippage_cost']:+,.2f} USDT  ({stats['slippage_pct']:+.2f}%)")
    lines.append(f"净收益:         {stats['net_profit']:+,.2f} USDT  ({stats['net_pct']:+.2f}%)")
    lines.append(f"年化收益率:     {stats['annualized']:+.2f}%")
    lines.append(f"资金费率次数:   {stats['total_count']} 次")
    lines.append(f"正费率次数:     {stats['positive_count']} 次 ({stats['positive_count']/stats['total_count']*100 if stats['total_count'] else 0:.1f}%)")
    lines.append(f"负费率次数:     {stats['negative_count']} 次 ({stats['negative_count']/stats['total_count']*100 if stats['total_count'] else 0:.1f}%)")
    lines.append(f"最大单笔收益:   {stats['max_profit']:+,.2f} USDT")
    lines.append(f"最大单笔亏损:   {stats['max_loss']:+,.2f} USDT")

    return "\n".join(lines)


def print_summary(all_results):
    """打印所有交易对的汇总对比表格"""
    print("\n" + "=" * 100)
    print("汇总对比")
    print("=" * 100)
    header = f"{'交易对':<10} {'净收益(USDT)':>14} {'收益率':>10} {'年化':>10} {'次数':>6} {'正/负':>10}"
    print(header)
    print("-" * len(header))

    for pair_key, stats in all_results.items():
        pair_name = stats.get("_name", pair_key)
        row = (
            f"{pair_name:<10} "
            f"{stats['net_profit']:>+13.2f} "
            f"{stats['net_pct']:>+9.2f}% "
            f"{stats['annualized']:>+9.2f}% "
            f"{stats['total_count']:>6d} "
            f"{stats['positive_count']}/{stats['negative_count']:>7d}"
        )
        print(row)

    print()


def print_detail(pair_key, pair_info, stats):
    """打印某交易对的逐笔明细"""
    print(f"\n--- {pair_info['name']} 逐笔明细 ---")
    print(f"{'时间':<19} {'费率':>10} {'价格':>12} {'费率收益':>12} {'手续费':>12} {'滑点':>12} {'净收益':>12} {'累计':>12}")
    print("-" * 115)

    cumulative = 0.0
    for d in stats["detail"]:
        cumulative += d["net_pnl"]
        print(
            f"{d['time']:<19} "
            f"{float(d['rate']):>10.6f} "
            f"{float(d['price']):>12.2f} "
            f"{d['funding_pnl']:>+11.2f} "
            f"{d['fee_pnl']:>+11.2f} "
            f"{d['slippage_pnl']:>+11.2f} "
            f"{d['net_pnl']:>+11.2f} "
            f"{cumulative:>+11.2f}"
        )


def main():
    parser = argparse.ArgumentParser(description="资金费率套利回测程序")
    parser.add_argument(
        "--pair", "-p",
        nargs="+",
        default=None,
        help="交易对（如 btcusdt ethusdt），默认全部",
    )
    parser.add_argument("--start", "-s", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", "-e", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument(
        "--capital", "-c", type=float, default=10000,
        help="总投入资金（USDT），默认 10000",
    )
    parser.add_argument(
        "--leverage", "-l", type=float, default=1.0,
        help="合约杠杆倍数（默认 1）。现货=合约仓位，保证金=仓位/杠杆",
    )
    parser.add_argument(
        "--futures-fee", type=float, default=None,
        help="合约手续费率（默认 0.0004）",
    )
    parser.add_argument(
        "--spot-fee", type=float, default=None,
        help="现货手续费率（默认 0.0004）",
    )
    parser.add_argument(
        "--slippage", type=float, default=None,
        help="滑点率（默认 0.0001）",
    )
    parser.add_argument(
        "--detail", "-d", action="store_true",
        help="显示逐笔明细",
    )
    parser.add_argument(
        "--with-bfusd", action="store_true",
        help="启用 BFUSD 年化收益（需 bfusd.db 数据）",
    )
    args = parser.parse_args()

    # 加载配置
    pairs, fees = load_config()

    # CLI 参数覆盖配置文件
    if args.futures_fee is not None:
        fees["futures_fee"] = args.futures_fee
    if args.spot_fee is not None:
        fees["spot_fee"] = args.spot_fee
    if args.slippage is not None:
        fees["slippage"] = args.slippage

    # 验证日期
    try:
        datetime.strptime(args.start, "%Y-%m-%d")
        datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError:
        print("错误: 日期格式错误，请使用 YYYY-MM-DD 格式")
        sys.exit(1)

    # 验证交易对
    if args.pair:
        selected = []
        for p in args.pair:
            pk = p.lower()
            if pk in pairs:
                selected.append(pk)
            else:
                print(f"警告: 未知交易对 '{p}'，可选: {', '.join(pairs.keys())}")
        pairs = {k: pairs[k] for k in selected}

    if not pairs:
        print("错误: 没有有效的交易对")
        sys.exit(1)

    # 检查数据库
    if not DB_PATH.exists():
        print("错误: 数据库文件不存在，请先运行 import_to_db.py 或 fetch_funding_rate_db.py")
        sys.exit(1)

    # 加载 BFUSD 数据（如果启用）
    bfusd_rates = {}
    if args.with_bfusd:
        if BFUSD_DB_PATH.exists():
            bfusd_rates = load_bfusd_rates(args.start, args.end)
            print(f"BFUSD 数据: {len(bfusd_rates)} 天")
        else:
            print("警告: bfusd.db 不存在，无法计算 BFUSD 收益")
            print("请先运行 fetch_bfusd_rate.py 获取 BFUSD 利率数据")

    # 运行回测
    all_results = {}
    print("=" * 60)
    print("资金费率套利回测结果" + ("（含 BFUSD）" if args.with_bfusd else ""))
    print("=" * 60)

    for pair_key, pair_info in pairs.items():
        pair_name = pair_info["name"]

        # 检查是否早于交易对上线时间
        launch_date = pair_info["time"]
        if args.start < launch_date:
            print(f"\n交易对 {pair_name} 于 {launch_date} 上线，开始日期已调整为 {launch_date}")
            actual_start = launch_date
        else:
            actual_start = args.start

        if actual_start > args.end:
            print(f"\n交易对 {pair_name}: 开始日期 ({actual_start}) 晚于结束日期 ({args.end})，跳过")
            continue

        records = load_log_data(pair_name, actual_start, args.end)
        if not records:
            print(f"\n交易对 {pair_name}: 指定时间段内无数据")
            continue

        stats = run_backtest(records, args.capital, args.leverage, fees,
                             with_bfusd=args.with_bfusd, bfusd_rates=bfusd_rates)
        stats["_name"] = pair_name
        all_results[pair_key] = stats

        print()
        print(format_result(pair_key, pair_info, stats, with_bfusd=args.with_bfusd))

        if args.detail:
            print_detail(pair_key, pair_info, stats)

    # 汇总对比
    if len(all_results) > 1:
        print_summary(all_results)


if __name__ == "__main__":
    main()

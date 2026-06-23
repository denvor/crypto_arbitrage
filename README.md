# 资金费率套利回测程序

**crypto Arbitrage** 是基于币安合约历史资金费率数据的回测工具，回测做空合约 + 做多现货的 Delta 中性套利策略。

[![GitHub](https://img.shields.io/badge/GitHub-crypto_arbitrage-181717?logo=github)](https://github.com/denvor/crypto_arbitrage)

## 快速开始

### WebUI（推荐）

```bash
# 安装依赖
pip install -r requirements.txt

# 初始化数据库
python scripts/initdb.py

# 从 CSV 导入历史数据（快速建立数据库）
python scripts/import_data.py

# 启动 Flask
python app.py

# 访问
# http://127.0.0.1:5000/maintenance   数据维护页面
# http://127.0.0.1:5000/backtest      回测页面
```

### 命令行

```bash
# 查看可导入的 CSV 文件
python scripts/import_data.py --list

# 导入全部数据到数据库
python scripts/import_data.py

# 只导入指定交易对
python scripts/import_data.py --pair BTCUSDT ETHUSDT

# 执行回测
python scripts/backtest.py --pair btcusdt --start 2024-01-01 --end 2024-12-31
```

## 策略原理

**资金费率套利**：币安合约每 8 小时结算一次资金费率。当费率为正时，多头方向支付空头方向费用；为负时相反。

套利策略：同时建立等值的**合约空头** + **现货多头**仓位，赚取正资金费率收益。价格波动在两个方向上相互抵消。

## 资金分配模型

| 杠杆 | 现货 | 合约保证金 | 合约仓位 | 说明 |
|------|------|-----------|---------|------|
| 1x | 5,000 | 5,000 | 5,000 | 现货 = 合约仓位 |
| 3x | 7,500 | 2,500 | 7,500 | 保证金 = 仓位 / 杠杆 |
| 5x | 8,333 | 1,667 | 8,333 | 杠杆越高，保证金占比越低 |

核心公式：`P = capital * leverage / (leverage + 1)`

保证现货价值始终等于合约仓位价值，实现严格等值对冲。

## 成本模型

- **手续费**：开仓 + 平仓各收一次，双边 = 2 × (futures_fee + spot_fee) × 仓位价值
- **滑点**：开仓 + 平仓各收一次，双边 = 2 × slippage × 仓位价值

默认配置见 `config.ini` 的 `[backtest]` 段：

```ini
[backtest]
futures_fee = 0.0002    # 合约手续费率 (0.02%)
spot_fee = 0.001        # 现货手续费率 (0.10%)
slippage = 0            # 滑点率 (0%)
```

## 命令行参数

```
--pair, -p        交易对 (btcusdt, btcusdc, ethusdt, ethusdc)，默认全部
--start, -s       开始日期 YYYY-MM-DD
--end, -e         结束日期 YYYY-MM-DD
--capital, -c     总投入资金 USDT，默认 10000
--leverage, -l    合约杠杆倍数，默认 1
--futures-fee     合约手续费率，覆盖配置文件
--spot-fee        现货手续费率，覆盖配置文件
--slippage        滑点率，覆盖配置文件
--detail, -d      显示逐笔明细
```

## 示例

### 单交易对回测

```bash
# BTCUSDT 2024 全年，1万资金，3倍杠杆
python scripts/backtest.py --pair btcusdt --start 2024-01-01 --end 2024-12-31 --leverage 3
```

### 多交易对对比

```bash
# 对比所有交易对 2024 上半年收益
python scripts/backtest.py --start 2024-01-01 --end 2024-06-30 --leverage 5
```

### 自定义费率

```bash
# 使用更低的手续费和滑点
python scripts/backtest.py --pair btcusdt --start 2024-01-01 --end 2024-12-31 \
  --futures-fee 0.0002 --spot-fee 0.0002 --slippage 0.00005
```

### 查看逐笔明细

```bash
# 查看某段时间每笔资金费率的明细
python scripts/backtest.py --pair btcusdt --start 2024-06-01 --end 2024-06-07 --detail
```

## 输出说明

```
资金费率套利回测结果
============================================================

交易对: BTCUSDT
期间: 2024-01-01 ~ 2024-06-30
总资金: 10,000.00 USDT
  现货: 7,500.00 USDT (75.0%)
  合约保证金: 2,500.00 USDT (25.0%)
合约杠杆: 3.0x | 合约仓位: 7,500.00 USDT
持有天数: 181.0 天

资金费率收益:   +592.48 USDT  (+5.92%)     # 费率 × 仓位价值累计
手续费:         -12.00 USDT  (-0.12%)       # 开仓+平仓双边手续费
滑点成本:       -1.50 USDT  (-0.01%)        # 开仓+平仓双边滑点
净收益:         +578.98 USDT  (+5.79%)      # 总收益
年化收益率:     +11.68%                       # 按持有天数年化

资金费率次数:   544 次
正费率次数:     530 次 (97.4%)
负费率次数:     14 次 (2.6%)
最大单笔收益:   +6.61 USDT
最大单笔亏损:   -0.28 USDT
```

## 数据文件

数据通过 `scripts/fetch_funding_rate_db.py` 获取（代理自动从 `config.ini` 的 `[proxy]` 段读取），存储在 `db/funding_rate.db` 中。

CSV 导出文件位于 `db/` 目录（`funding_rate_*.csv`、`*_rate.csv`），可用 `scripts/import_data.py` 快速导入，也可用 `scripts/export_data.py` 从数据库导出。

每个交易对在 `config.ini` 中有 `time` 字段，表示该交易对上线日期。回测时会自动检查，早于上线日期的数据会被跳过。

## 文件结构

```
crypto_arbitrage/
  config.ini              # 全局配置（交易对、代理、回测参数）
  app.py                  # Flask WebUI 入口
  requirements.txt        # Python 依赖（requests, flask）
  uwsgi/                  # uWSGI 配置
    uwsgi.ini
  scripts/                # Python 脚本
    initdb.py             # 首次使用前初始化数据库
    utils.py              # 共享工具（代理、配置、DB 统计、任务管理）
    backtest.py           # 回测程序
    export_data.py        # 导出数据库到 CSV
    import_data.py        # 从 CSV 导入数据库
    fetch_funding_rate_db.py  # 数据获取程序（资金费率）
    fetch_bfusd_rate.py   # BFUSD 利率获取
  templates/              # WebUI 页面模板
    maintenance.html      # 数据维护页面
    backtest.html         # 回测页面
  db/                     # SQLite 数据库和 CSV 导出
    funding_rate.db
    bfusd.db
    funding_rate_*.csv    # 资金费率导出文件
    *_rate.csv            # 收益型资产利率导出文件
  old/                    # 已废弃的旧版脚本
```

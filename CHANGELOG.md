# CHANGELOG

## v2.3.0 - Flask WebUI（2026-05-09）

### 新增功能

1. **Flask WebUI** — 通过 `uwsgi --ini uwsgi/uwsgi.ini` 启动，提供两个页面：
   - `/maintenance` — 数据维护页面：展示 4 个交易对 + BFUSD 的数据范围（起始/结束日期、记录数），支持增量更新，后台线程执行，前端轮询进度
   - `/backtest` — 回测页面：交易对多选、日期范围、资金/杠杆/手续费/滑点参数配置、BFUSD 开关，返回汇总对比表和逐笔明细

2. **资金费率更新 end_ts 修复** — 原代码 `end_ts = yesterday 00:00` 只覆盖凌晨一瞬间，漏掉 08:00 和 16:00 结算点。修复为 `today 00:00 - 1ms + 10s 缓冲`，覆盖昨天全天。`fundingTime` 有毫秒偏移（如 `timestamp + 3ms`），加 10s 缓冲避免被过滤。

### 新增文件

| 文件 | 说明 |
|------|------|
| `app.py` | Flask WebUI 入口，包含数据维护和回测路由 |
| `scripts/utils.py` | 共享工具模块（代理配置、config 加载、DB 统计、文件存储的任务管理） |
| `templates/maintenance.html` | 数据维护页面模板 |
| `templates/backtest.html` | 回测页面模板 |
| `requirements.txt` | Python 依赖（requests, flask） |

### 修改文件

| 文件 | 变更 |
|------|------|
| `scripts/fetch_funding_rate_db.py` | 复用 `utils.get_proxies()`；修复 `end_ts` 覆盖全天 |
| `scripts/fetch_bfusd_rate.py` | 复用 `utils.get_proxies()` |
| `CLAUDE.md` | 更新文档 |
| `README.md` | 更新文档 |

### 技术细节

- **多进程任务管理**：uWSGI 多进程模式下内存字典无法共享，改用 `uwsgi/jobs/` 目录存储 job 状态（JSON 文件），支持跨进程查询进度
- **代理配置**：`get_proxies()` 统一从环境变量 > config.ini 读取，提取到共享模块
- **任务进度**：后台线程更新 JSON 文件，前端每 1.5 秒轮询 `/api/fetch/status/<job_id>`

---

## v2.2.0 - 增量获取优化与代理统一（2026-05-01）

### Bug 修复

1. **BFUSD 增量获取提前终止** — 修复 `fetch_bfusd_rate.py` 从当前时间向过去逐批获取时，即使数据库已有数据仍继续请求的问题。新增逻辑：当获取到的最新日期 `<=` 数据库截止日时立即停止，避免无效请求。
2. **BFUSD 数据不更新已有日期** — 移除 `fetch_bfusd_rate.py` 中的 UPDATE 逻辑，改为只 INSERT 新数据，遇到已有日期直接停止。

### 改进

3. **`fetch_funding_rate_db.py` 代理配置统一** — 新增 `get_proxies()` 函数，代理来源优先级：环境变量 > config.ini `[proxy]` 段，与 `fetch_bfusd_rate.py` 保持一致。

---

## v2.1.0 - BFUSD 年化收益（2026-04-30）

### 新增功能

将币安 BFUSD（Binance Flexible Savings）年化利率收益加入回测，用 `--with-bfusd` 参数启用。

### 新增文件

| 文件 | 说明 |
|------|------|
| `fetch_bfusd_rate.py` | 从币安 API 获取 BFUSD 年化利率并写入 `bfusd.db`（HMAC-SHA256 签名认证，增量更新） |
| `bfusd.db` | SQLite 数据库，包含 `bfusd_rate` 表（date, apr） |
| `docs/bfusd-api-params.md` | BFUSD API 参数文档、签名规则、分页策略、数据采集报告 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `backtest.py` | 新增 `--with-bfusd` 参数、`load_bfusd_rates()` 函数、BFUSD 收益计算逻辑 |
| `CLAUDE.md` | 更新文档：新增 BFUSD 使用说明、运行命令、架构说明 |

### 收益计算

```
BFUSD 每日收益 = 当日 APR / 365 × 合约保证金
```

年化利率由币安每日更新，回测时按记录日期取对应 APR 逐日累加。

### 使用方式

```bash
# 获取 BFUSD 利率数据（自动增量更新到昨天）
HTTPS_PROXY=http://127.0.0.1:20171 python fetch_bfusd_rate.py

# 运行回测（含 BFUSD 收益）
python backtest.py --pair btcusdt --start 2025-01-01 --end 2025-12-31 --with-bfusd
```

### BFUSD API 参数与分页策略

#### API 端点

```
GET https://api.binance.com/sapi/v1/bfusd/history/rateHistory
```

#### 认证方式

HMAC-SHA256 签名，参数按 key 排序后拼接为 query string，签名追加 `signature` 字段。
**注意**：必须手动构建完整 URL，不能使用 `requests` 的 `params` 参数（URL 编码差异导致签名不匹配）。

#### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `timestamp` | long | 是 | 当前毫秒时间戳 |
| `startTime` | long | 否 | 查询起始时间（毫秒戳），返回 >= 该时间的数据 |
| `endTime` | long | 否 | 查询截止时间（毫秒戳），返回 <= 该时间的数据 |
| `size` | int | 否 | 每页条数，默认 10，最大 100 |
| `current` | int | 否 | 页码，默认 1 |

#### 分页策略

经过测试验证：
- `current` + `size` 分页参数**不被支持**（返回签名无效错误）
- `startTime` + `endTime` 范围查询**支持**，但时间跨度不超过 6 个月
- 最优策略：**用 `endTime` 逐批获取**，每次获取 10 条后用最后一条的 `time-1` 作为下一批的 `endTime`

```python
# 策略：用 endTime 逐批获取
last_end_time = int(datetime.now().timestamp() * 1000)
while True:
    params = {"timestamp": ts, "endTime": str(last_end_time)}
    url = make_signed_url(api_base, params, api_secret)
    resp = requests.get(url, ...)
    rows = resp.json().get("rows", [])
    if not rows:
        break
    # 处理 rows...
    last_end_time = rows[-1]["time"] - 1  # 最后一条 - 1 作为下一批
    time.sleep(3)  # 批次间延迟
```

#### 数据采集结果

| 指标 | 值 |
|------|------|
| 总记录数 | **526 条** |
| 时间范围 | 2024-11-20 ~ 2026-04-29 |
| 数据完整性 | **100%**（526 天零缺失） |
| 请求批次 | 54 批（每批 10 条） |
| 总耗时 | ~185 秒 |
| 请求间隔 | 3 秒/批 |

#### Bug 修复

1. **requests params 签名不匹配** — `requests` 库的 `params` 参数会自动对值做 URL 编码，导致签名计算与请求发送时的 query string 不一致。解决方案：手动构建完整 URL，将签名后的参数直接拼接到 URL 中
2. **endTime 范围超限** — `startTime` + `endTime` 组合查询的时间跨度不能超过 6 个月，使用 `endTime` 逐批策略可避免此限制
3. **旧数据 APR 值异常** — 2024-11-20 ~ 2024-11-24 期间 APR 值在 0.1~0.35 之间（10%~35%），后续稳定在 0.01~0.08 之间。需确认是否为 BFUSD 上线初期的异常高利率
4. **BFUSD 收益重复计算（严重）** — 回测遍历每笔资金费率记录时都会计算 BFUSD 收益，但同一天有 3 条记录（8:00/16:00/00:00），导致 BFUSD 收益被重复计算 3 倍。修复：用集合记录已计算的日期，同一天只算一次。修复前后对比（1x 杠杆）：BFUSD 收益从 664.22 → 221.41 USDT，净收益从 936.02 → 493.21 USDT

---

## v2.0.0 - 数据迁移与架构升级

### 变更概述

将数据层从纯文本 `.log` 文件迁移至 SQLite 数据库，提升查询效率与数据管理能力。

### 新增文件

| 文件 | 说明 |
|------|------|
| `import_to_db.py` | 将 `files/coin*.log` 历史数据导入 SQLite |
| `fetch_funding_rate_db.py` | 从币安 API 获取资金费率并写入数据库（替代 `fetch_funding_rate.py`） |
| `funding_rate.db` | SQLite 数据库，包含 `funding_rate` 表及索引 |
| `CHANGELOG.md` | 版本变更记录 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `backtest.py` | `load_log_data()` 改为查询 SQLite 数据库，替代文本文件解析 |
| `backtest.py` | `run_backtest()` 修复合约仓位价值固定错误，改为随价格动态计算 |
| `CLAUDE.md` | 更新架构说明与命令 |
| `config.ini` | 交易对配置从独立 section 改为 `[pairs]` 段 key=value 格式 |

### 数据库结构

```sql
CREATE TABLE funding_rate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,        -- 交易对 (BTCUSDT 等)
    time TEXT NOT NULL,        -- 时间字符串
    timestamp INTEGER NOT NULL, -- 毫秒时间戳
    funding_rate TEXT NOT NULL, -- 资金费率
    price TEXT NOT NULL         -- 标记价格
);
CREATE INDEX idx_pair ON funding_rate(pair);
CREATE INDEX idx_timestamp ON funding_rate(timestamp);
```

### 数据量

| 交易对 | 记录数 | 时间范围 |
|--------|--------|----------|
| BTCUSDC | 2,541 | 2024-01-03 ~ 2026-04-28 |
| BTCUSDT | 7,267 | 2019-09-10 ~ 2026-04-28 |
| ETHUSDC | 2,541 | 2024-01-03 ~ 2026-04-28 |
| ETHUSDT | 7,033 | 2019-11-27 ~ 2026-04-28 |
| **总计** | **19,382** | |

### 数据完整性

| 交易对 | 记录数 | 完整性 | 说明 |
|--------|--------|--------|------|
| BTCUSDC | 2,541 / 2,541 | ✓ 100% | 完整 |
| BTCUSDT | 7,267 / 7,269 | ✓ 99.97% | 2019-09-10 上线首日 API 无数据（2 条） |
| ETHUSDC | 2,541 / 2,541 | ✓ 100% | 完整 |
| ETHUSDT | 7,030 / 7,035 | ⚠ 99.93% | 2020-10 部分日期 API 无数据（5 条），已补回 90 条 |

### Bug 修复

1. **markPrice 冗余请求** — `fundingRate` API 返回的数据已包含 `markPrice` 字段，不再需要额外请求 klines API 获取价格，请求量减少约 50%
2. **交易对大小写不一致** — 修复了 `fetch_funding_rate_db.py` 中 `pair` 字段存储了小写（`btcusdt`）而查询使用大写（`BTCUSDT`）导致数据无法正确匹配的问题
3. **输出缓冲** — 添加 `sys.stdout.flush()` 确保长时间运行的数据抓取任务能实时输出进度
4. **网络代理** — 支持 `HTTPS_PROXY` / `https_proxy` 环境变量，适配代理网络环境
5. **空价格导致崩溃** — 修复 `rate.get("markPrice", "N/A")` 在 API 返回空字符串时不 fallback 的问题，改为 `rate.get("markPrice") or "N/A"`
6. **旧数据 markPrice 为空** — 2020 年部分数据 API 返回的 `markPrice` 为空字符串，`fetch_funding_rate_db.py` 增加了回退逻辑：当 `markPrice` 为空时通过 klines API 获取价格
7. **合约仓位价值固定错误（严重）** — 修复 `run_backtest()` 中 `position` 作为固定值的问题。正确逻辑：合约数量 = 初始仓位价值 / 初始价格（固定），每次费率时仓位价值 = 合约数量 × 当时价格（随价格波动），资金费率收益 = 费率 × 当时仓位价值。2019-2025 年 BTC 价格从 ~10k 涨到 ~100k，此修复使费率收益从 3,962 USDT 修正为 17,084 USDT

### 使用方式

```bash
# 导入历史日志文件到数据库
python import_to_db.py

# 从 API 获取最新数据写入数据库
HTTPS_PROXY=http://127.0.0.1:20171 python fetch_funding_rate_db.py

# 运行回测（自动从数据库读取）
python backtest.py --pair btcusdt --start 2025-01-01 --end 2026-04-28
```

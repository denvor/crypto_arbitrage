# BFUSD API 参数文档

## API 端点

```
GET https://api.binance.com/sapi/v1/bfusd/history/rateHistory
```

## 认证方式

HMAC-SHA256 签名认证。

### 签名步骤

1. 将请求参数（除 `signature` 外）按 key 字母顺序排序
2. 用 `&` 连接 `key=value` 形成 query string
3. 用 API Secret 对该 query string 做 HMAC-SHA256 计算
4. 将 hex 编码的签名结果作为 `signature` 参数追加到 URL 末尾

### 签名代码

```python
import hashlib, hmac

def make_signed_url(base_url, params, secret):
    """手动构建带签名的完整 URL"""
    # 按 key 排序后拼接（不含 signature）
    sorted_params = sorted((k, v) for k, v in params.items() if k != "signature")
    query_string = "&".join(f"{k}={v}" for k, v in sorted_params)
    # HMAC-SHA256 签名
    signature = hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    # 手动构建完整 URL
    return f"{base_url}?{query_string}&signature={signature}"
```

### ⚠️ 关键注意事项

**必须手动构建完整 URL，不能使用 `requests` 的 `params` 参数。**

原因：`requests` 库的 `params` 参数会自动对值做 URL 编码（如空格变 `%20`），导致签名计算时的 query string 与请求实际发送的 query string 不一致，返回签名无效错误。

```python
# ❌ 错误：requests 会自动编码，导致签名不匹配
requests.get(url, params=params, ...)

# ✅ 正确：手动构建完整 URL
url = make_signed_url(base_url, params, secret)
requests.get(url, ...)
```

## 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `timestamp` | long | 是 | 当前毫秒时间戳 |
| `startTime` | long | 否 | 查询起始时间（毫秒戳），返回 >= 该时间的数据 |
| `endTime` | long | 否 | 查询截止时间（毫秒戳），返回 <= 该时间的数据 |
| `size` | int | 否 | 每页条数，默认 10，最大 100 |
| `current` | int | 否 | 页码，默认 1 |

## 返回数据

```json
{
  "rows": [
    {
      "time": 1748188800000,
      "annualPercentageRate": "0.01960331"
    }
  ],
  "total": 526,
  "currentPage": 1
}
```

- `rows`: 利率记录数组，按时间降序排列
- `total`: 符合条件的总记录数
- `currentPage`: 当前页码

## 分页策略

### 测试结论

| 策略 | 结果 | 说明 |
|------|------|------|
| 无参数请求 | ✅ 返回 10 条 | 最新 10 条数据 |
| 仅 `startTime` | ✅ 返回 10 条 | >= 该时间的最新 10 条 |
| `startTime` + `endTime` | ✅ 返回 10 条 | 时间范围内的最新 10 条（范围 ≤ 6 个月） |
| `current` + `size` | ❌ 签名无效 | 该接口不支持分页参数 |
| `startTime` + `endTime` + `current` + `size` | ❌ 签名无效 | 同上 |

### 推荐策略：用 `endTime` 逐批获取

```python
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
    time.sleep(3)  # 批次间延迟，避免限流
```

**原理**：每次请求 `<= endTime` 的最新 10 条数据，然后用最后一条记录的 `time-1` 作为下一批的 `endTime`，逐步向前推进，直到返回空数据。

### 为什么不使用 `startTime` + `endTime` 范围查询？

虽然该方式也支持，但存在限制：
- 时间跨度不能超过 6 个月（180 天）
- 每次请求最多返回 10 条（不支持 `size` 参数）
- 需要手动分批次处理 6 个月以上的数据

相比之下，`endTime` 逐批策略更简单可靠，无需关心时间范围限制。

## 数据采集报告

### 采集结果

| 指标 | 值 |
|------|------|
| 总记录数 | **526 条** |
| 时间范围 | 2024-11-20 ~ 2026-04-29 |
| 数据完整性 | **100%**（526 天零缺失） |
| 请求批次 | 54 批（每批 10 条） |
| 总耗时 | ~185 秒 |
| 请求间隔 | 3 秒/批 |

### 数据样本

**最近 5 条：**

| 日期 | APR |
|------|-----|
| 2026-04-29 | 0.01960331 |
| 2026-04-28 | 0.01932230 |
| 2026-04-27 | 0.01882977 |
| 2026-04-26 | 0.01882724 |
| 2026-04-25 | 0.01877180 |

**最早 5 条：**

| 日期 | APR |
|------|-----|
| 2024-11-20 | 0.10150012 |
| 2024-11-21 | 0.22181928 |
| 2024-11-22 | 0.23396701 |
| 2024-11-23 | 0.35107252 |
| 2024-11-24 | 0.23930815 |

### 数据异常说明

2024-11-20 ~ 2024-11-24 期间 APR 值在 0.1~0.35 之间（10%~35%），远高于后续稳定值（1%~8%）。

可能原因：
1. BFUSD 上线初期的高利率促销
2. 数据源在上线初期的特殊定价机制
3. 需要与币安官方确认

## 请求限流

- 每次请求 IP 权重 150
- 建议批次间延迟 ≥ 3 秒
- 遇到 429 状态码时等待 5 秒后重试

## 相关代码

- `fetch_bfusd_rate.py` — 数据采集脚本
- `bfusd.db` — SQLite 数据库
- `backtest.py` — 回测工具（`--with-bfusd` 参数启用）

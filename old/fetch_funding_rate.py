import os
import configparser
from datetime import datetime, timedelta  # 新增timedelta
import requests
import configparser
from datetime import datetime, timedelta
import time  # 新增time模块导入

def get_funding_rate(symbol, startTime, endTime):
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    params = {
        "symbol": symbol,
        "startTime": startTime,
        "endTime": endTime,
        "limit": 1000
    }
    try:
        # 获取资金费率
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        funding_rates = resp.json()
        
        # 获取对应时间点的价格
        price_url = "https://fapi.binance.com/fapi/v1/klines"
        for rate in funding_rates:
            start_time = time.time()  # 记录开始时间
            print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(rate['fundingTime']) / 1000)))
            # 获取资金费率时间点的1分钟K线
            price_params = {
                "symbol": symbol,
                "interval": "1m",
                "startTime": rate['fundingTime'],
                "limit": 1
            }
            price_resp = requests.get(price_url, params=price_params)
            if price_resp.status_code == 200:
                kline = price_resp.json()
                if kline:
                    # 添加价格信息到返回结果
                    rate['price'] = kline[0][4]  # 收盘价
            #time.sleep(1)
            end_time = time.time()  # 记录结束时间
            print(f"执行时间: {end_time - start_time:.2f}秒")  # 打印执行时间

        return funding_rates
    except Exception as e:
        print(f"请求异常: {e}")
    return []

# 删除环境变量校验代码
# 时间处理优化（使用datetime）
def main():
    config = configparser.ConfigParser()
    config.read('d:\\Work\\taoli\\config.ini')

    # 确保files目录存在
    os.makedirs('d:\\Work\\taoli\\files', exist_ok=True)
    # 手动打开文件
    log_file = open('d:\\Work\\taoli\\files\\coin2025.log', 'a', encoding='utf-8')

    # 按月循环处理
    for month in range(1, 4):  # 1月到6月
        year = 2025
        start_date = datetime(year, month, 1)
        # 计算每个月的最后一天
        if month == 12:
            end_date = datetime(year, month, 31) + timedelta(days=1) - timedelta(seconds=1)
        else:
            end_date = datetime(year, month + 1, 1) - timedelta(seconds=1)
        print(f"开始日期: {start_date}, 结束日期: {end_date}")
        # 转换为毫秒时间戳
        start_time = int(start_date.timestamp() * 1000)
        end_time = int(end_date.timestamp() * 1000)
        
        print(f"\n处理 {start_date.year}年{month}月数据:")
        
        # 遍历配置文件中的每个部分
        for section in config.sections():
            symbol = config.get(section, 'name').strip('"')
            print(f"\n交易对: {symbol}")
            funding_rates = get_funding_rate(symbol, start_time, end_time)
            if funding_rates:
                print(f"资金费率信息：")
                for rate in funding_rates:
                    funding_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(rate['fundingTime']) / 1000))
                    print(f"时间: {funding_time}, 时间戳: {rate['fundingTime']}, 资金费率: {rate['fundingRate']}, 价格: {rate.get('price', 'N/A')}")
                    # 将JSON格式写入日志文件
                    log_data = {
                        "交易对": symbol,
                        "时间": funding_time,
                        "时间戳": rate['fundingTime'],
                        "资金费率": rate['fundingRate'],
                        "价格": rate.get('price', 'N/A')
                    }
                    log_file.write(f"{log_data}\n")

    # 手动关闭文件
    log_file.close()

if __name__ == "__main__":
    start_time = time.time()  # 记录开始时间
    main()
    end_time = time.time()  # 记录结束时间
    print(f"脚本执行时间: {end_time - start_time:.2f}秒")  # 打印执行时间

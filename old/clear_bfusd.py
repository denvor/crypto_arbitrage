#!/usr/bin/env python3
import sqlite3
conn = sqlite3.connect('bfusd.db')
conn.execute('DELETE FROM bfusd_rate')
conn.commit()
print(f'清空完成，剩余: {conn.execute("SELECT COUNT(*) FROM bfusd_rate").fetchone()[0]} 条')
conn.close()

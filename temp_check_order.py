#!/usr/bin/env python3
import sqlite3
import time

conn = sqlite3.connect('/root/wechat-decrypt/decrypted/message/biz_message_0.db')
cur = conn.cursor()

# 订单创建时间 09:26:40 = 约 1777262800
search_from = 1777260000
print(f'Searching from: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(search_from))}')

cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
tables = [row[0] for row in cur.fetchall()]

all_messages = []
for table in tables:
    try:
        cur.execute(f'''
            SELECT create_time, local_type, message_content 
            FROM "{table}" 
            WHERE create_time > ?
            ORDER BY create_time DESC 
            LIMIT 30
        ''', (search_from,))
        rows = cur.fetchall()
        for row in rows:
            base_type = row[1] & 0xFFFFFFFF if row[1] else 0
            all_messages.append((row[0], base_type, row[2], table))
    except Exception as e:
        pass

print(f'Found {len(all_messages)} messages after 09:26')
for msg in all_messages[:10]:
    dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(msg[0]))
    print(f'  {dt}, type={msg[1]}, table={msg[3][:20]}...')

# Check specifically for payment type (49 with sub_type 5)
payment_msgs = [m for m in all_messages if m[1] == 49]
print(f'\nPayment messages (type 49): {len(payment_msgs)}')
for msg in payment_msgs:
    dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(msg[0]))
    print(f'  {dt}, table={msg[3][:20]}...')

conn.close()
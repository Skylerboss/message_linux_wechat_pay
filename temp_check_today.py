#!/usr/bin/env python3
import sqlite3
import time

conn = sqlite3.connect('/root/wechat-decrypt/decrypted/message/biz_message_0.db')
cur = conn.cursor()

# Get today's timestamp range (2026-04-29)
today_start = 1777252800  # 09:20:00
print(f'Looking for messages after {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(today_start))}')

cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
tables = [row[0] for row in cur.fetchall()]

all_today_messages = []
for table in tables:
    try:
        cur.execute(f'''
            SELECT create_time, local_type, message_content 
            FROM "{table}" 
            WHERE create_time > ?
            ORDER BY create_time DESC 
            LIMIT 20
        ''', (today_start,))
        rows = cur.fetchall()
        for row in rows:
            base_type = row[1] & 0xFFFFFFFF if row[1] else 0
            all_today_messages.append((row[0], base_type, row[2]))
    except Exception as e:
        pass

print(f'\nFound {len(all_today_messages)} messages today')
# Print unique message types
types = set()
for msg in all_today_messages:
    types.add(msg[1])
print(f'Message types found: {types}')

# Check for payment type (49)
payment_msgs = [m for m in all_today_messages if m[1] == 49]
print(f'Payment messages (type 49): {len(payment_msgs)}')

conn.close()
#!/usr/bin/env python3
import sqlite3
import time

conn = sqlite3.connect('/root/wechat-decrypt/decrypted/message/biz_message_0.db')
cur = conn.cursor()

# Get current time
now = int(time.time())
print(f'Current time: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))}')

cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
tables = [row[0] for row in cur.fetchall()]

# Get latest message from each table
latest_time = 0
for table in tables:
    try:
        cur.execute(f'SELECT MAX(create_time) FROM "{table}"')
        max_time = cur.fetchone()[0] or 0
        if max_time > latest_time:
            latest_time = max_time
    except:
        pass

print(f'Latest message time: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest_time))}')
print(f'Time diff from now: {now - latest_time} seconds')

# Search for messages in last hour (including payment type)
search_from = now - 3600
print(f'\nSearching from 1 hour ago: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(search_from))}')

all_messages = []
for table in tables:
    try:
        cur.execute(f'''
            SELECT create_time, local_type, message_content 
            FROM "{table}" 
            WHERE create_time > ?
            ORDER BY create_time DESC 
            LIMIT 50
        ''', (search_from,))
        rows = cur.fetchall()
        for row in rows:
            base_type = row[1] & 0xFFFFFFFF if row[1] else 0
            all_messages.append((row[0], base_type, row[2], table))
    except Exception as e:
        pass

print(f'Found {len(all_messages)} messages in last hour')
for msg in all_messages[:10]:
    dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(msg[0]))
    print(f'  {dt}, type={msg[1]}, table={msg[3][:20]}...')

# Payment messages
payment_msgs = [m for m in all_messages if m[1] == 49]
print(f'\nPayment messages (type 49): {len(payment_msgs)}')

conn.close()
#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('/root/wechat-decrypt/decrypted/message/biz_message_0.db')
cur = conn.cursor()

# Get table names
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
tables = [row[0] for row in cur.fetchall()]

# Check for payment messages (local_type with base_type 49, sub_type 5)
print('Searching for payment messages (type 49, sub_type 5)...')
for table in tables:
    try:
        # local_type & 0xFFFFFFFF = 49 means system message
        # sub_type = 5 means payment
        cur.execute(f'''
            SELECT create_time, local_type, message_content 
            FROM "{table}" 
            WHERE (local_type & 0xFFFFFFFF) = 49
            ORDER BY create_time DESC 
            LIMIT 5
        ''')
        rows = cur.fetchall()
        if rows:
            print(f'\n{table}: {len(rows)} payment messages')
            for row in rows[:3]:
                import time
                dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row[0]))
                print(f'  Time: {dt}, type: {row[1]}')
    except Exception as e:
        pass

conn.close()
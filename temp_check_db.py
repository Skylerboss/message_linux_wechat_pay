#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('/root/wechat-decrypt/decrypted/message/biz_message_0.db')
cur = conn.cursor()

# Get table names
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
tables = [row[0] for row in cur.fetchall()]
print(f'Tables found: {len(tables)}')

# Check recent records
for table in tables[:3]:
    try:
        cur.execute(f'SELECT create_time, local_type FROM "{table}" ORDER BY create_time DESC LIMIT 3')
        print(f'\n{table}:')
        for row in cur.fetchall():
            print(row)
    except Exception as e:
        print(f'Error: {e}')

conn.close()
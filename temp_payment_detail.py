#!/usr/bin/env python3
import sqlite3
import time
import zstandard as zstd

conn = sqlite3.connect('/root/wechat-decrypt/decrypted/message/biz_message_0.db')
cur = conn.cursor()

# 查找最近的支付消息 (type 49)
search_from = 1777260000  # 09:26:00

cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
tables = [row[0] for row in cur.fetchall()]

zstd_decompressor = zstd.ZstdDecompressor()

for table in tables:
    try:
        cur.execute(f'''
            SELECT create_time, local_type, message_content
            FROM "{table}"
            WHERE create_time > ? AND (local_type & 0xFFFFFFFF) = 49
            ORDER BY create_time DESC
            LIMIT 10
        ''', (search_from,))
        rows = cur.fetchall()
        for create_time, local_type, message_content in rows:
            dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(create_time))
            print(f'Time: {dt}, type: {local_type}')
            # 尝试解码内容
            try:
                if message_content:
                    data = message_content.tobytes() if hasattr(message_content, 'tobytes') else message_content
                    try:
                        text = zstd_decompressor.decompress(data).decode('utf-8', errors='replace')
                        print(f'Content preview: {text[:200]}')
                    except:
                        print(f'Content (raw): {data[:100]}')
            except Exception as e:
                print(f'Error: {e}')
            print('---')
    except Exception as e:
        print(f'Table {table}: {e}')

conn.close()
#!/usr/bin/env python3
from pathlib import Path
import sqlite3
import sys

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from csf.paths import get_batch_db_path

conn = sqlite3.connect(get_batch_db_path())
cursor = conn.execute('SELECT status, COUNT(*) FROM analysis_status GROUP BY status')
print('Analysis status counts:')
for row in cursor.fetchall():
    print(f'  {row[0]}: {row[1]}')

cursor = conn.execute('SELECT COUNT(*) FROM analysis_status')
total = cursor.fetchone()[0]
print(f'Total: {total} videos tracked')
conn.close()


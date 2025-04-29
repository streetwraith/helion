import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import sys

if len(sys.argv) < 3:
    print("Usage: python import.py <csv_filename> <db_url>")
    sys.exit(1)

csv_filename = sys.argv[1]
db_url = sys.argv[2]

table = 'a4e_market_history_volume'

df = pd.read_csv(csv_filename, delimiter=';')

conn = psycopg2.connect(db_url)

columns = list(df.columns)
values = [tuple(x) for x in df.to_numpy()]

insert_stmt = f"""
    INSERT INTO {table} ({', '.join(columns)})
    VALUES %s
    ON CONFLICT DO NOTHING
"""

with conn:
    with conn.cursor() as cur:
        execute_values(cur, insert_stmt, values)

conn.close()

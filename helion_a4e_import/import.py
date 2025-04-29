import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from getpass import getpass
import sys

if len(sys.argv) < 2:
    print("Usage: python import.py <csv_filename>")
    sys.exit(1)

csv_filename = sys.argv[1]
password = getpass("Enter your db password: ")

table = 'a4e_market_history_volume'

df = pd.read_csv(csv_filename, delimiter=';')

conn = psycopg2.connect(
    dbname='postgres',
    user='postgres',
    password=password,
    host='localhost',
    port='5432'
)

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

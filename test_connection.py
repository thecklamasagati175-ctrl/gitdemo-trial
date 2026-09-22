#!/usr/bin/env python3
"""Quick standalone check that .env credentials can reach the database.
Run this BEFORE wiring the server into Claude Desktop, so connection
problems are easy to debug on their own.

Usage:
    python3 test_connection.py
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.environ.get("DB_HOST")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_NAME = os.environ.get("DB_NAME") or None

print(f"Connecting to {DB_USER}@{DB_HOST}:{DB_PORT} (db={DB_NAME or '<none>'})...")

try:
    import pymysql
except ImportError:
    print("pymysql not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        connect_timeout=10,
    )
    with conn.cursor() as cur:
        cur.execute("SELECT VERSION()")
        version = cur.fetchone()[0]
        print(f"Connected OK. Server version: {version}")

        cur.execute("SHOW DATABASES")
        dbs = [r[0] for r in cur.fetchall()]
        system_schemas = {"information_schema", "mysql", "performance_schema", "sys"}
        user_dbs = [d for d in dbs if d not in system_schemas]
        print(f"Visible databases ({len(user_dbs)}): {user_dbs}")

        if DB_NAME:
            cur.execute("SHOW TABLES")
            tables = [r[0] for r in cur.fetchall()]
            print(f"Tables in '{DB_NAME}' ({len(tables)}): {tables[:20]}{' ...' if len(tables) > 20 else ''}")
        elif user_dbs:
            sample_db = user_dbs[0]
            cur.execute("SHOW TABLES FROM `%s`" % sample_db)
            tables = [r[0] for r in cur.fetchall()]
            print(f"(No DB_NAME set — that's fine, you'll pass `database` per tool call.)")
            print(f"Example — tables in '{sample_db}' ({len(tables)}): {tables[:20]}{' ...' if len(tables) > 20 else ''}")
    conn.close()
except Exception as e:
    print(f"Connection FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

#!/usr/bin/env python3
"""
simplify-mysql-mcp
-------------------
An MCP server exposing a set of tools for working with a MariaDB/MySQL
database, modeled after a "full toolkit" connector: listing tables,
describing schema, reading rows, inserting/updating/deleting rows,
altering schema, running raw SQL, and taking backups.

Configure via a `.env` file (see .env.example) and point Claude Desktop's
config at this script (see README.md).
"""

import os
import sys
import json
import shutil
import subprocess
import datetime
import decimal
from typing import Any

import pymysql
import pymysql.cursors
from dotenv import load_dotenv

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

load_dotenv()

DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "") or None
DB_READ_ONLY = os.environ.get("DB_READ_ONLY", "false").strip().lower() == "true"
ALLOWED_TABLES = {
    t.strip() for t in os.environ.get("DB_ALLOWED_TABLES", "").split(",") if t.strip()
}
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_backup_dir_raw = os.environ.get("BACKUP_DIR", ".")
# Resolve relative to this script's own folder, not the process's cwd —
# Claude Desktop launches the server with an unpredictable working
# directory, so a relative path here would otherwise write backups
# somewhere you'd never think to look. Default is "." so backup.sql sits
# right next to server.py, same as a simple single-backup workflow.
BACKUP_DIR = (
    _backup_dir_raw
    if os.path.isabs(_backup_dir_raw)
    else os.path.join(SCRIPT_DIR, _backup_dir_raw)
)
MAX_ROWS = int(os.environ.get("MAX_ROWS", "500"))

WRITE_KEYWORDS = (
    "insert", "update", "delete", "alter", "drop", "truncate",
    "create", "replace", "grant", "revoke",
)


def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        autocommit=False,
    )


def check_table_allowed(table: str):
    if ALLOWED_TABLES and table not in ALLOWED_TABLES:
        raise PermissionError(
            f"Table '{table}' is not in DB_ALLOWED_TABLES. "
            f"Allowed: {sorted(ALLOWED_TABLES)}"
        )


def check_write_allowed(context: str = "this operation"):
    if DB_READ_ONLY:
        raise PermissionError(
            f"DB_READ_ONLY is enabled — {context} is blocked. "
            "Set DB_READ_ONLY=false in .env to allow writes."
        )


def check_sql_read_only(sql: str):
    lowered = sql.strip().lower()
    if DB_READ_ONLY and any(lowered.startswith(k) or f" {k} " in lowered for k in WRITE_KEYWORDS):
        raise PermissionError(
            "DB_READ_ONLY is enabled — only SELECT/SHOW/DESCRIBE statements are allowed."
        )


def json_safe(value: Any) -> Any:
    """Make DB values JSON-serializable."""
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    return value


def rows_to_json(rows: list[dict]) -> str:
    clean = [{k: json_safe(v) for k, v in row.items()} for row in rows]
    return json.dumps(clean, indent=2, default=str)


server = Server("simplify-mysql-mcp")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_databases",
            description="List all databases visible to this DB user on the server.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_tables",
            description=(
                "List all tables in a database. Pass `database` to pick which one "
                "(you have multiple); if omitted, uses DB_NAME from .env if set."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name. Omit to use the default."}
                },
            },
        ),
        Tool(
            name="describe_table",
            description="Get column names, types, keys, and defaults for a given table.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "database": {"type": "string", "description": "Database the table lives in. Omit to use the default."},
                },
                "required": ["table"],
            },
        ),
        Tool(
            name="fetch_rows",
            description=(
                "Fetch rows from a table, optionally filtered by exact column values, "
                "with optional limit/offset."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "database": {"type": "string", "description": "Database the table lives in. Omit to use the default."},
                    "filters": {
                        "type": "object",
                        "description": "Column: value pairs for exact-match WHERE filtering.",
                    },
                    "limit": {"type": "integer", "default": 100},
                    "offset": {"type": "integer", "default": 0},
                },
                "required": ["table"],
            },
        ),
        Tool(
            name="insert_row",
            description="Insert a new row into a table.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "database": {"type": "string", "description": "Database the table lives in. Omit to use the default."},
                    "values": {"type": "object", "description": "Column: value pairs to insert."},
                },
                "required": ["table", "values"],
            },
        ),
        Tool(
            name="update_row",
            description="Update row(s) in a table matching filter column values.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "database": {"type": "string", "description": "Database the table lives in. Omit to use the default."},
                    "filters": {"type": "object", "description": "Column: value pairs identifying rows to update."},
                    "values": {"type": "object", "description": "Column: value pairs to set."},
                },
                "required": ["table", "filters", "values"],
            },
        ),
        Tool(
            name="delete_row",
            description="Delete row(s) from a table matching filter column values.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "database": {"type": "string", "description": "Database the table lives in. Omit to use the default."},
                    "filters": {"type": "object", "description": "Column: value pairs identifying rows to delete."},
                },
                "required": ["table", "filters"],
            },
        ),
        Tool(
            name="add_column",
            description="Add a new column to a table.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "database": {"type": "string", "description": "Database the table lives in. Omit to use the default."},
                    "column": {"type": "string"},
                    "definition": {
                        "type": "string",
                        "description": "SQL column definition, e.g. 'VARCHAR(255) NOT NULL DEFAULT \"\"'",
                    },
                },
                "required": ["table", "column", "definition"],
            },
        ),
        Tool(
            name="run_query",
            description="Run a READ-ONLY SQL query (SELECT/SHOW/DESCRIBE/EXPLAIN only).",
            inputSchema={
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        ),
        Tool(
            name="execute_sql",
            description=(
                "Run ANY SQL statement — SELECT, JOIN, INSERT, UPDATE, DELETE, ALTER, "
                "CREATE, DROP, TRUNCATE. Use with care; blocked entirely when DB_READ_ONLY=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        ),
        Tool(
            name="backup_database",
            description="Create a full backup of the database using mysqldump.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Optional filename; defaults to a timestamped name.",
                    }
                },
            },
        ),
        Tool(
            name="restore_database",
            description=(
                "Restore the database from a previously created .sql backup file. "
                "Overwrites current data with whatever is in the backup — use to "
                "revert a batch of changes that went wrong. If no filename is given, "
                "restores from the most recently created .sql file in the backup folder."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Backup filename to restore from. Omit to use the newest backup.",
                    }
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = await dispatch(name, arguments or {})
        return [TextContent(type="text", text=result)]
    except PermissionError as e:
        return [TextContent(type="text", text=f"Permission denied: {e}")]
    except pymysql.MySQLError as e:
        return [TextContent(type="text", text=f"Database error: {e}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def dispatch(name: str, args: dict) -> str:
    if name == "list_databases":
        return tool_list_databases()
    if name == "list_tables":
        return tool_list_tables(args.get("database"))
    if name == "describe_table":
        return tool_describe_table(args["table"], args.get("database"))
    if name == "fetch_rows":
        return tool_fetch_rows(
            args["table"], args.get("database"), args.get("filters"),
            args.get("limit", 100), args.get("offset", 0),
        )
    if name == "insert_row":
        return tool_insert_row(args["table"], args.get("database"), args["values"])
    if name == "update_row":
        return tool_update_row(args["table"], args.get("database"), args["filters"], args["values"])
    if name == "delete_row":
        return tool_delete_row(args["table"], args.get("database"), args["filters"])
    if name == "add_column":
        return tool_add_column(args["table"], args.get("database"), args["column"], args["definition"])
    if name == "run_query":
        return tool_run_query(args["sql"])
    if name == "execute_sql":
        return tool_execute_sql(args["sql"])
    if name == "backup_database":
        return tool_backup_database(args.get("filename"))
    if name == "restore_database":
        return tool_restore_database(args.get("filename"))
    raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def tool_list_databases() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            rows = cur.fetchall()
            dbs = [list(r.values())[0] for r in rows]
            # Hide MySQL/MariaDB system schemas by default — noise for a
            # "which of my databases" question.
            system_schemas = {"information_schema", "mysql", "performance_schema", "sys"}
            dbs = [d for d in dbs if d not in system_schemas]
            return json.dumps(dbs, indent=2)
    finally:
        conn.close()


def tool_list_tables(database: str | None) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            db = database or DB_NAME
            if db:
                cur.execute("SHOW TABLES FROM `%s`" % _safe_ident(db))
            else:
                cur.execute("SHOW TABLES")
            rows = cur.fetchall()
            tables = [list(r.values())[0] for r in rows]
            if ALLOWED_TABLES:
                tables = [t for t in tables if t in ALLOWED_TABLES]
            return json.dumps(tables, indent=2)
    finally:
        conn.close()


def tool_describe_table(table: str, database: str | None) -> str:
    check_table_allowed(table)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DESCRIBE %s" % _qualify(table, database))
            rows = cur.fetchall()
            return rows_to_json(rows)
    finally:
        conn.close()


def tool_fetch_rows(table: str, database: str | None, filters: dict | None, limit: int, offset: int) -> str:
    check_table_allowed(table)
    limit = min(int(limit or 100), MAX_ROWS)
    offset = max(int(offset or 0), 0)
    sql = "SELECT * FROM %s" % _qualify(table, database)
    params: list = []
    if filters:
        clauses = []
        for col, val in filters.items():
            clauses.append("`%s` = %%s" % _safe_ident(col))
            params.append(val)
        sql += " WHERE " + " AND ".join(clauses)
    sql += " LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return rows_to_json(rows)
    finally:
        conn.close()


def tool_insert_row(table: str, database: str | None, values: dict) -> str:
    check_table_allowed(table)
    check_write_allowed("insert_row")
    cols = list(values.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    col_list = ", ".join("`%s`" % _safe_ident(c) for c in cols)
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (_qualify(table, database), col_list, placeholders)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, list(values.values()))
            conn.commit()
            return json.dumps({"inserted_id": cur.lastrowid, "rows_affected": cur.rowcount})
    finally:
        conn.close()


def tool_update_row(table: str, database: str | None, filters: dict, values: dict) -> str:
    check_table_allowed(table)
    check_write_allowed("update_row")
    if not filters:
        raise ValueError("update_row requires at least one filter to avoid a full-table update.")
    set_clause = ", ".join("`%s` = %%s" % _safe_ident(c) for c in values.keys())
    where_clause = " AND ".join("`%s` = %%s" % _safe_ident(c) for c in filters.keys())
    sql = "UPDATE %s SET %s WHERE %s" % (_qualify(table, database), set_clause, where_clause)
    params = list(values.values()) + list(filters.values())

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return json.dumps({"rows_affected": cur.rowcount})
    finally:
        conn.close()


def tool_delete_row(table: str, database: str | None, filters: dict) -> str:
    check_table_allowed(table)
    check_write_allowed("delete_row")
    if not filters:
        raise ValueError("delete_row requires at least one filter to avoid a full-table delete.")
    where_clause = " AND ".join("`%s` = %%s" % _safe_ident(c) for c in filters.keys())
    sql = "DELETE FROM %s WHERE %s" % (_qualify(table, database), where_clause)
    params = list(filters.values())

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return json.dumps({"rows_affected": cur.rowcount})
    finally:
        conn.close()


def tool_add_column(table: str, database: str | None, column: str, definition: str) -> str:
    check_table_allowed(table)
    check_write_allowed("add_column")
    sql = "ALTER TABLE %s ADD COLUMN `%s` %s" % (_qualify(table, database), _safe_ident(column), definition)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
            return json.dumps({"status": "ok", "sql": sql})
    finally:
        conn.close()


def tool_run_query(sql: str) -> str:
    lowered = sql.strip().lower()
    if not lowered.startswith(("select", "show", "describe", "desc", "explain")):
        raise PermissionError("run_query only allows SELECT/SHOW/DESCRIBE/EXPLAIN. Use execute_sql for writes.")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchmany(MAX_ROWS)
            return rows_to_json(rows)
    finally:
        conn.close()


def tool_execute_sql(sql: str) -> str:
    check_sql_read_only(sql)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            lowered = sql.strip().lower()
            if lowered.startswith(("select", "show", "describe", "desc", "explain")):
                rows = cur.fetchmany(MAX_ROWS)
                return rows_to_json(rows)
            conn.commit()
            return json.dumps({"rows_affected": cur.rowcount})
    finally:
        conn.close()


def tool_backup_database(filename: str | None) -> str:
    # Backups are read-only in effect (mysqldump only reads), so they're allowed
    # even when DB_READ_ONLY=true.
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if not filename:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        db_label = DB_NAME or "database"
        filename = f"{db_label}_{ts}.sql"
    if not filename.endswith(".sql"):
        filename += ".sql"
    out_path = os.path.join(BACKUP_DIR, filename)
    if shutil.which("mysqldump") is None:
        raise RuntimeError(
            "mysqldump not found on PATH. Install the MySQL/MariaDB client tools "
            "(e.g. `brew install mariadb` or `apt install mariadb-client`)."
        )

    cmd = [
        "mysqldump",
        "-h", DB_HOST,
        "-P", str(DB_PORT),
        "-u", DB_USER,
        f"-p{DB_PASSWORD}",
    ]
    if DB_NAME:
        cmd.append(DB_NAME)
    else:
        cmd.append("--all-databases")

    with open(out_path, "w") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        if os.path.exists(out_path):
            os.remove(out_path)
        raise RuntimeError(f"mysqldump failed: {proc.stderr.strip()}")

    size = os.path.getsize(out_path)
    return json.dumps({"status": "ok", "file": os.path.abspath(out_path), "size_bytes": size})


def tool_restore_database(filename: str | None) -> str:
    # Restoring is a write-heavy op by nature (it overwrites the DB), so
    # respect DB_READ_ONLY here — you don't want an accidental revert on a
    # database you've locked down as read-only.
    check_write_allowed("restore_database")

    if filename:
        in_path = filename if os.path.isabs(filename) else os.path.join(BACKUP_DIR, filename)
        if not in_path.endswith(".sql"):
            in_path += ".sql"
    else:
        # No filename given: use the most recently modified .sql in BACKUP_DIR.
        if not os.path.isdir(BACKUP_DIR):
            raise FileNotFoundError(f"Backup folder not found: {BACKUP_DIR}")
        candidates = [
            os.path.join(BACKUP_DIR, f)
            for f in os.listdir(BACKUP_DIR)
            if f.endswith(".sql")
        ]
        if not candidates:
            raise FileNotFoundError(f"No .sql backup files found in {BACKUP_DIR}")
        in_path = max(candidates, key=os.path.getmtime)

    if not os.path.isfile(in_path):
        raise FileNotFoundError(f"Backup file not found: {in_path}")

    if shutil.which("mysql") is None:
        raise RuntimeError(
            "mysql client not found on PATH. Install the MySQL/MariaDB client tools "
            "(same package that provides mysqldump)."
        )

    cmd = [
        "mysql",
        "-h", DB_HOST,
        "-P", str(DB_PORT),
        "-u", DB_USER,
        f"-p{DB_PASSWORD}",
    ]
    if DB_NAME:
        cmd.append(DB_NAME)

    with open(in_path, "r") as f:
        proc = subprocess.run(cmd, stdin=f, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Restore failed: {proc.stderr.strip()}")

    return json.dumps({
        "status": "ok",
        "restored_from": os.path.abspath(in_path),
    })


def _safe_ident(name: str) -> str:
    """Reject identifiers that could break out of backticks (basic guard)."""
    if "`" in name or name.strip() == "":
        raise ValueError(f"Invalid identifier: {name!r}")
    return name


def _qualify(table: str, database: str | None) -> str:
    """Build a backtick-quoted, optionally db-qualified table identifier.

    - If `database` is given explicitly, use `database`.`table`.
    - Otherwise fall back to DB_NAME from .env, if set.
    - Otherwise just `table` (relies on the DB user's default database,
      which will fail with "No database selected" if there isn't one —
      in which case the caller should pass `database` explicitly).
    """
    table = _safe_ident(table)
    db = database or DB_NAME
    if db:
        return "`%s`.`%s`" % (_safe_ident(db), table)
    return "`%s`" % table


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    if not DB_USER or not DB_PASSWORD:
        print("ERROR: DB_USER / DB_PASSWORD not set. Copy .env.example to .env and fill it in.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(main())

import os
import subprocess
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
import mysql.connector
from mysql.connector import Error as MySQLError

load_dotenv()

ALLOW_WRITES = os.getenv("ALLOW_WRITES", "false").lower() == "true"

mcp = FastMCP("mysql-explorer")


def get_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        port=int(os.getenv("MYSQL_PORT")),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"),
    )


def get_real_columns(conn, table_name: str) -> list:
    cursor = conn.cursor()
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    return [row[0] for row in cursor.fetchall()]


def writes_blocked():
    if not ALLOW_WRITES:
        return "Writes are turned off. Set ALLOW_WRITES=true in manifest.json to enable."
    return None


# ---------- READ TOOLS ----------

@mcp.tool()
def list_tables() -> list:
    """List all tables in the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES")
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()
    return tables


@mcp.tool()
def describe_table(table_name: str) -> list:
    """Get column names and types for a given table."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"DESCRIBE `{table_name}`")
    columns = cursor.fetchall()
    conn.close()
    return columns


@mcp.tool()
def fetch_rows(table_name: str, filters: dict = None, limit: int = 20) -> dict:
    """Fetch rows from a table, optionally filtered by exact column values.
    Example filters: {"status": "active"}
    """
    filters = filters or {}
    conn = get_connection()
    try:
        real_columns = get_real_columns(conn, table_name)
        for col in filters:
            if col not in real_columns:
                return {"error": f"Column '{col}' does not exist on '{table_name}'"}
        where = ""
        values = []
        if filters:
            where = "WHERE " + " AND ".join(f"`{c}` = %s" for c in filters)
            values = list(filters.values())
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT * FROM `{table_name}` {where} LIMIT %s", values + [limit])
        rows = cursor.fetchall()
        return {"row_count": len(rows), "rows": rows}
    except MySQLError as e:
        return {"error": str(e)}
    finally:
        conn.close()


@mcp.tool()
def run_query(sql: str, limit: int = 100) -> dict:
    """Run a READ-ONLY SQL query (SELECT/SHOW/DESCRIBE only). Use this for
    anything fetch_rows can't do, like JOINs, COUNT, GROUP BY, etc."""
    cleaned = sql.strip().rstrip(";")
    first_word = cleaned.split()[0].lower() if cleaned.split() else ""
    if first_word not in ("select", "show", "describe", "desc", "explain") or ";" in cleaned:
        return {"error": "Only a single SELECT/SHOW/DESCRIBE/EXPLAIN statement is allowed."}
    if first_word == "select" and "limit" not in cleaned.lower():
        cleaned += f" LIMIT {limit}"
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(cleaned)
        rows = cursor.fetchall()
        return {"row_count": len(rows), "rows": rows}
    except MySQLError as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ---------- WRITE TOOLS (need ALLOW_WRITES=true) ----------

@mcp.tool()
def insert_row(table_name: str, data: dict) -> dict:
    """Insert a new row. data example: {"name": "Jane", "email": "jane@x.com"}"""
    err = writes_blocked()
    if err:
        return {"error": err}
    if not data:
        return {"error": "'data' cannot be empty."}
    conn = get_connection()
    try:
        real_columns = get_real_columns(conn, table_name)
        for col in data:
            if col not in real_columns:
                return {"error": f"Column '{col}' does not exist on '{table_name}'"}
        cols = ", ".join(f"`{c}`" for c in data)
        placeholders = ", ".join(["%s"] * len(data))
        cursor = conn.cursor()
        cursor.execute(f"INSERT INTO `{table_name}` ({cols}) VALUES ({placeholders})", list(data.values()))
        conn.commit()
        return {"success": True, "inserted_id": cursor.lastrowid}
    except MySQLError as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


@mcp.tool()
def update_row(table_name: str, data: dict, filters: dict) -> dict:
    """Update row(s). REQUIRES filters so you can't update every row by accident.
    data example: {"status": "inactive"}  filters example: {"id": 42}"""
    err = writes_blocked()
    if err:
        return {"error": err}
    if not data or not filters:
        return {"error": "'data' and 'filters' are both required (filters can't be empty)."}
    conn = get_connection()
    try:
        real_columns = get_real_columns(conn, table_name)
        for col in list(data.keys()) + list(filters.keys()):
            if col not in real_columns:
                return {"error": f"Column '{col}' does not exist on '{table_name}'"}
        set_clause = ", ".join(f"`{c}` = %s" for c in data)
        where_clause = " AND ".join(f"`{c}` = %s" for c in filters)
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE `{table_name}` SET {set_clause} WHERE {where_clause}",
            list(data.values()) + list(filters.values()),
        )
        conn.commit()
        return {"success": True, "rows_affected": cursor.rowcount}
    except MySQLError as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


@mcp.tool()
def delete_row(table_name: str, filters: dict, confirm: bool = False) -> dict:
    """Delete row(s). REQUIRES filters and confirm=True as safety checks.
    filters example: {"id": 42}"""
    err = writes_blocked()
    if err:
        return {"error": err}
    if not filters:
        return {"error": "'filters' cannot be empty — can't delete every row."}
    if not confirm:
        return {"error": "Set confirm=True to actually run this deletion."}
    conn = get_connection()
    try:
        real_columns = get_real_columns(conn, table_name)
        for col in filters:
            if col not in real_columns:
                return {"error": f"Column '{col}' does not exist on '{table_name}'"}
        where_clause = " AND ".join(f"`{c}` = %s" for c in filters)
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM `{table_name}` WHERE {where_clause}", list(filters.values()))
        conn.commit()
        return {"success": True, "rows_deleted": cursor.rowcount}
    except MySQLError as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


@mcp.tool()
def add_column(table_name: str, column_name: str, column_type: str) -> dict:
    """Add a new column to a table. column_type examples: "VARCHAR(255)",
    "INT", "TEXT", "DATE", "BOOLEAN" """
    err = writes_blocked()
    if err:
        return {"error": err}
    conn = get_connection()
    try:
        real_columns = get_real_columns(conn, table_name)
        if column_name in real_columns:
            return {"error": f"Column '{column_name}' already exists on '{table_name}'"}
        cursor = conn.cursor()
        cursor.execute(f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {column_type}")
        conn.commit()
        return {"success": True, "message": f"Added column '{column_name}' to '{table_name}'"}
    except MySQLError as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


@mcp.tool()
def execute_sql(sql: str, confirm: bool = False) -> dict:
    """Run ANY SQL statement — SELECT, JOIN, ALTER, CREATE, DROP, TRUNCATE,
    UPDATE, DELETE, anything. Only one statement per call (no semicolons
    stacking multiple commands).

    For DROP, TRUNCATE, or an UPDATE/DELETE with no WHERE clause, you must
    also pass confirm=True — these are the operations that can destroy data
    with no undo, so this is your one safety check before they run.
    """
    err = writes_blocked()
    if err:
        return {"error": err}

    cleaned = sql.strip().rstrip(";")
    if ";" in cleaned:
        return {"error": "Only one statement at a time (no ';' stacking multiple commands)."}

    lowered = cleaned.lower()
    is_dangerous = (
        lowered.startswith("drop") or
        lowered.startswith("truncate") or
        (lowered.startswith("update") and " where " not in lowered) or
        (lowered.startswith("delete") and " where " not in lowered)
    )
    if is_dangerous and not confirm:
        return {
            "error": (
                "This looks like it would affect an entire table with no "
                "undo (DROP/TRUNCATE, or UPDATE/DELETE with no WHERE). "
                "Re-run with confirm=True if you're sure."
            )
        }

    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(cleaned)
        if cursor.with_rows:
            rows = cursor.fetchall()
            return {"row_count": len(rows), "rows": rows}
        conn.commit()
        return {"success": True, "rows_affected": cursor.rowcount}
    except MySQLError as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


# ---------- BACKUP TOOL ----------

@mcp.tool()
def backup_database() -> dict:
    """Create a full backup of the database using mysqldump. Saves a
    timestamped .sql file next to the server script. Run this before
    any risky change (ALTER, DROP, bulk UPDATE/DELETE)."""
    mysqldump_path = os.getenv("MYSQLDUMP_PATH", "mysqldump")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.dirname(os.path.abspath(__file__))
    backup_file = os.path.join(backup_dir, f"backup_{timestamp}.sql")

    try:
        with open(backup_file, "w") as f:
            result = subprocess.run(
                [
                    mysqldump_path,
                    "-h", os.getenv("MYSQL_HOST"),
                    "-P", os.getenv("MYSQL_PORT"),
                    "-u", os.getenv("MYSQL_USER"),
                    f"-p{os.getenv('MYSQL_PASSWORD')}",
                    os.getenv("MYSQL_DATABASE"),
                ],
                stdout=f, stderr=subprocess.PIPE, text=True,
            )
        if result.returncode != 0:
            os.remove(backup_file)
            return {"error": result.stderr}
        return {"success": True, "message": f"Backup saved to {backup_file}"}
    except FileNotFoundError:
        return {
            "error": (
                f"Couldn't find mysqldump at '{mysqldump_path}'. Add "
                "MYSQLDUMP_PATH to manifest.json's env with the full path."
            )
        }


if __name__ == "__main__":
    mcp.run(transport="stdio")
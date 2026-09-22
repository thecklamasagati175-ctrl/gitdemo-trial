# simplify-mysql-mcp

An MCP server that gives Claude Desktop a full toolkit for your MariaDB/MySQL
database at `stage.simplify.co.tz`, mirroring the tools your existing
connector has:

| Tool | What it does |
|---|---|
| `list_databases` | List all databases visible to your DB user |
| `list_tables` | List all tables in a database (pass `database` to pick which) |
| `describe_table` | Show columns, types, keys for a table |
| `fetch_rows` | Read rows, with optional filters, limit/offset |
| `insert_row` | Insert a new row |
| `update_row` | Update row(s) matching filters |
| `delete_row` | Delete row(s) matching filters |
| `add_column` | ALTER TABLE ... ADD COLUMN |
| `run_query` | Read-only SQL (SELECT/SHOW/DESCRIBE/EXPLAIN) |
| `execute_sql` | Any SQL — SELECT/INSERT/UPDATE/DELETE/ALTER/DROP/TRUNCATE |
| `backup_database` | Dumps the DB to a `.sql` file via `mysqldump` |
| `restore_database` | Restores the DB from a `.sql` backup — reverts a batch of changes |

### Working with multiple databases

`DB_NAME` in `.env` is left blank on purpose, since your `intern_ds` account
sees more than one database. Every table-level tool (`list_tables`,
`describe_table`, `fetch_rows`, `insert_row`, `update_row`, `delete_row`,
`add_column`) takes an optional `database` argument — just tell Claude which
database you mean ("show me the `orders` table in `simplify_prod`") and it
passes that through. Start with `list_databases` to see what's visible, then
`list_tables` (with `database` set) to see what's in each one.

If you *do* mostly work in one database, you can still set `DB_NAME` in
`.env` as a default — `database` on any tool call overrides it for that
call only.

## 1. Install dependencies

You need Python 3.10+ and `mysqldump` (for backups) on your machine.

```bash
cd simplify-mysql-mcp
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`mysqldump` comes with the MySQL/MariaDB client tools:
- macOS: `brew install mariadb` (or `mysql-client`)
- Debian/Ubuntu: `sudo apt install mariadb-client`
- Windows: install MySQL Community Server/Client, or MariaDB client, and
  make sure `mysqldump.exe` is on your PATH.

## 2. Configure credentials

Your `.env` is already filled in with the credentials you gave me:

```
DB_HOST=stage.simplify.co.tz
DB_PORT=33066
DB_USER=intern_ds
DB_PASSWORD=********  (already set in .env)
```

Since this is a **staging** database and your account is named `intern_ds`,
consider setting `DB_READ_ONLY=true` in `.env` if you mainly want to
explore/query data and don't need Claude making writes or schema changes.
You can flip it back to `false` any time you need `insert_row`,
`update_row`, `delete_row`, `add_column`, or write-style `execute_sql`
calls.

`.env` is already in `.gitignore` — never commit it or paste the password
anywhere public.

## 3. Test the connection (before wiring into Claude Desktop)

```bash
python3 test_connection.py
```

You should see the server version and a list of visible tables. Fix any
connection errors (network/VPN access to `stage.simplify.co.tz`, firewall,
correct password) here first — it's much easier to debug standalone than
through Claude Desktop's logs.

## 4. Wire it into Claude Desktop

There are two ways to install this. **Option A is recommended** — there's a currently-known bug where dragging/double-clicking a `.mcpb` file crashes the install dialog on some Windows builds, so the unpacked route sidesteps that entirely.

### Option A: Install Unpacked Extension (recommended, no zipping needed)

1. In Claude Desktop: **Settings → Extensions → Advanced settings → Install Unpacked Extension** (exact wording may vary slightly by version).
2. Point it at this project folder (the one containing `manifest.json` and `server.py`).
3. Claude Desktop reads `manifest.json`, shows you the tool list, and installs it — using the `venv` you created in step 1 above.

Requires the `venv/` folder from step 1 to already exist inside this project folder (it's how `manifest.json` finds the Python interpreter with `pymysql`/`mcp` installed).

### Option B: Pack it into a real `.mcpb` file

If you'd rather have a single `.mcpb` file (like your old `mysql-mcp-tutorial.mcpb`) you can share or drag in:

```bash
npm install -g @anthropic-ai/mcpb
cd simplify-mysql-mcp
mcpb pack
```

This bundles everything (including your `venv`) into one `.mcpb` archive you can double-click to install. Note: because of the Windows install-dialog bug mentioned above, if double-clicking doesn't open an install prompt, fall back to Option A instead.

### Option C: Manual config (what we did originally)

Still works if you'd rather not use the extension system — see the JSON snippet further below.


- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add an entry under `mcpServers` (create the file/section if it doesn't
exist), using the **absolute path** to this project:

```json
{
  "mcpServers": {
    "simplify-mysql": {
      "command": "/absolute/path/to/simplify-mysql-mcp/venv/bin/python3",
      "args": ["/absolute/path/to/simplify-mysql-mcp/server.py"]
    }
  }
}
```

(On Windows, `command` would be something like
`C:\\path\\to\\simplify-mysql-mcp\\venv\\Scripts\\python.exe`.)

Fully quit and reopen Claude Desktop. You should see "simplify-mysql" show
up as a connected tool (hammer/plug icon), with all 9 tools listed above
available to Claude.

## 5. Safety notes

- **`intern_ds` sounds like a limited-privilege account** — the database
  itself will enforce whatever GRANTs that user has, regardless of what
  this server allows. If it only has SELECT privileges, write tools will
  simply fail with a permission error from MySQL.
- `DB_ALLOWED_TABLES` in `.env` lets you restrict the server to a specific
  allowlist of tables (comma-separated), on top of whatever the DB user
  can already see.
- `update_row` / `delete_row` require at least one filter — they refuse to
  run an unfiltered UPDATE/DELETE that would hit the whole table.
- `execute_sql` is intentionally powerful (matches your existing
  connector's `execute_sql` tool) — set `DB_READ_ONLY=true` if you don't
  want Claude able to run arbitrary writes/DDL against a shared company
  database.
- Backups land in `BACKUP_DIR` (default: the project folder itself, same
  as your old `mysql-mcp-tutorial` setup — you'll see `backup.sql`-style
  files sitting right next to `server.py`).
- To revert a bulk change gone wrong: just ask Claude to run
  `restore_database`. With no filename it grabs the most recently created
  `.sql` file in `BACKUP_DIR` automatically — so the usual flow is
  "backup before a risky change → make the change → if it's wrong, restore".
  Restore is blocked when `DB_READ_ONLY=true`, same as other write tools.

## Troubleshooting

- **"mysqldump not found on PATH"** → install MySQL/MariaDB client tools
  (see step 1).
- **Connection refused / timeout** → you likely need VPN/network access to
  `stage.simplify.co.tz`; confirm you can reach it the same way your
  existing connector does.
- **Access denied for user** → double check the password wasn't truncated
  when copied into `.env`.
- Claude Desktop not picking up the server → check its logs (Settings →
  Developer, or the log files in the same folder as the config file) for
  the exact stderr the script printed.

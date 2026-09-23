"""
Run the saved SQL queries in queries/ against the DuckDB database.

    python run_query.py            # list the available queries
    python run_query.py 02         # run one query (by number or name)
    python run_query.py all        # run every query

The database is opened read-only, so queries can't change the data.
"""

import sys
from pathlib import Path

import duckdb

PROJECT_DIR = Path(__file__).parent
DATABASE_FILE = PROJECT_DIR / "database" / "energy_finance.duckdb"
QUERY_DIR = PROJECT_DIR / "queries"


def question(sql):
    """The first comment line after '-- Question:' (plus continuation lines)."""
    lines = []
    for line in sql.splitlines():
        if line.startswith("-- Question:"):
            lines.append(line.removeprefix("-- Question:").strip())
        elif lines and line.startswith("--") and line.removeprefix("--").startswith("   "):
            lines.append(line.removeprefix("--").strip())
        elif lines:
            break
    return " ".join(lines)


def main():
    queries = sorted(QUERY_DIR.glob("*.sql"))
    choice = sys.argv[1] if len(sys.argv) > 1 else None

    if choice is None:
        print("Available queries (run with: python run_query.py <number>, or 'all'):\n")
        for path in queries:
            print(f"  {path.stem}\n      {question(path.read_text())}\n")
        return

    selected = queries if choice == "all" else [p for p in queries if p.stem.startswith(choice) or p.stem == choice]
    if not selected:
        sys.exit(f"No query matches '{choice}'. Run 'python run_query.py' to list them.")
    if not DATABASE_FILE.exists():
        sys.exit("Database not found. Run 'python load_database.py' first.")

    con = duckdb.connect(str(DATABASE_FILE), read_only=True)
    for path in selected:
        sql = path.read_text()
        print(f"=== {path.stem} ===\n{question(sql)}\n")
        result = con.sql(sql)
        if result.fetchone() is None:
            print("(no rows yet)\n")
        else:
            con.sql(sql).show(max_width=250, max_rows=100)
            print()
    con.close()


if __name__ == "__main__":
    main()

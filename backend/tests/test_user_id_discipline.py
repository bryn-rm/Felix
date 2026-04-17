"""
Regression test: every raw SQL query in app/api/** must include 'user_id'
in its text to prevent accidental cross-user data leakage.

The backend uses the Supabase service role (bypasses RLS), so every query
MUST scope by user_id. This test greps for raw db.query / db.query_one /
db.execute calls in API route files and asserts the SQL contains 'user_id'.

Allowed exceptions:
  - Queries against tables that are inherently non-user-scoped (e.g. eval_runs)
  - Queries where user_id is enforced via a helper (e.g. db.insert/db.update/db.upsert
    with user_id in the data dict — those are checked by the helper's column list)
"""

import ast
import pathlib


# Tables that are intentionally not user-scoped
_EXEMPT_TABLES = {"eval_runs", "admin_audit"}

# Specific SQL fragments that are known-safe without user_id scoping.
# Each entry is a substring that must appear in the SQL for the exemption to apply.
_EXEMPT_SQL_FRAGMENTS = {
    "select id from ai_calls where id =",       # PK existence check (eval.py)
    "select count(*) as cnt",                    # aggregate budget check (rate_limit.py)
    "where  parse_error = true",                 # admin-only: parse errors (eval.py, gated by _require_admin)
    "coalesce(prompt_version",                   # admin-only: prompt version stats (eval.py, gated by _require_admin)
}

# db functions that take a table + dict (user_id is in the dict, not the SQL string)
_DICT_BASED_HELPERS = {"insert", "update", "upsert"}


def _extract_raw_sql_calls(filepath: pathlib.Path) -> list[tuple[int, str, str]]:
    """Parse a Python file and extract (lineno, func_name, sql_string) for
    calls like db.query("SELECT ..."), db.query_one("SELECT ..."), db.execute("SELECT ...").
    """
    source = filepath.read_text()
    tree = ast.parse(source, filename=str(filepath))
    results = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match db.query, db.query_one, db.execute
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "db":
            continue
        func_name = func.attr
        if func_name not in ("query", "query_one", "execute"):
            continue

        # Extract the first positional argument (the SQL string)
        if not node.args:
            continue
        sql_arg = node.args[0]

        # Handle string literals and f-strings
        sql_text = ""
        if isinstance(sql_arg, ast.Constant) and isinstance(sql_arg.value, str):
            sql_text = sql_arg.value
        elif isinstance(sql_arg, ast.JoinedStr):
            # f-string — extract the constant parts
            parts = []
            for val in sql_arg.values:
                if isinstance(val, ast.Constant):
                    parts.append(str(val.value))
            sql_text = "".join(parts)

        if sql_text.strip():
            results.append((node.lineno, func_name, sql_text))

    return results


def test_all_api_queries_include_user_id():
    """Every raw db.query/query_one/execute call in app/api/ must reference user_id."""
    api_dir = pathlib.Path(__file__).parent.parent / "app" / "api"
    assert api_dir.is_dir(), f"Expected API directory at {api_dir}"

    violations = []

    for py_file in sorted(api_dir.glob("*.py")):
        if py_file.name.startswith("__"):
            continue

        for lineno, func_name, sql_text in _extract_raw_sql_calls(py_file):
            sql_lower = sql_text.lower()

            # Skip if the SQL already references user_id
            if "user_id" in sql_lower:
                continue

            # Skip queries against exempt tables
            if any(table in sql_lower for table in _EXEMPT_TABLES):
                continue

            # Skip specific known-safe SQL fragments
            if any(frag in sql_lower for frag in _EXEMPT_SQL_FRAGMENTS):
                continue

            violations.append(
                f"{py_file.name}:{lineno} — db.{func_name}() missing user_id: "
                f"{sql_text.strip()[:120]}"
            )

    assert not violations, (
        "Found raw SQL queries without user_id scoping:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )

#!/usr/bin/env python3
"""Minimal pre-migration schema reader used by compatibility tests."""

import json
import os
import re
import sqlite3
import sys


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "schema":
        return 2
    table = sys.argv[2]
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table) is None:
        return 2
    connection = sqlite3.connect(os.environ["HEALTH_DB"])
    try:
        columns = [
            {
                "index": row[0],
                "name": row[1],
                "type": row[2],
                "not_null": bool(row[3]),
                "default": row[4],
                "primary_key": bool(row[5]),
            }
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        ]
    finally:
        connection.close()
    print(json.dumps({"table": table, "columns": columns}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

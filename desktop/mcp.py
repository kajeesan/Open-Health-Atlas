"""Bundled stdio entry point; the existing local MCP server owns the protocol.

This module deliberately does not start the desktop app or a web listener.
The client chooses a database and timezone using the server's existing flags.
"""

import argparse
from pathlib import Path
import runpy
import sys


def main() -> int:
    # Direct script execution places this directory first on sys.path, where
    # mcp.py would shadow the installed SDK package. The native helper uses
    # isolated Python (-I); retain that package resolution for source checks.
    if sys.path and Path(sys.path[0]).resolve() == Path(__file__).resolve().parent:
        sys.path.pop(0)
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from desktop.mcp_config import ConnectionConfigurationError, workspace_database
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workspace", type=Path)
    options, remaining = parser.parse_known_args()
    if options.workspace is not None:
        try:
            if any(arg == "--database" or arg.startswith("--database=") for arg in remaining):
                raise ConnectionConfigurationError("Choose either a workspace or a database.")
            database = workspace_database(options.workspace)
        except ConnectionConfigurationError as error:
            print(str(error), file=sys.stderr)
            return 2
        # Resolve once per connection. Never silently switch a running session.
        sys.argv = [sys.argv[0], "--database", str(database), *remaining]
    server = root / "scripts" / "openhealthatlas_mcp.py"
    if not server.is_file():
        print("The local AI connection is incomplete. Reinstall Open Health Atlas.", file=sys.stderr)
        return 2
    # Keep the original script's __file__ for its bounded worker subprocesses.
    # No diagnostic text may be written to stdout: it carries the MCP protocol.
    namespace = runpy.run_path(str(server), run_name="openhealthatlas_bundled_mcp")
    return namespace["main"]()


if __name__ == "__main__":
    raise SystemExit(main())

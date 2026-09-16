"""Private, disposable results for exact reads; canonical records stay read-only.

Keys describe the coherent SQLite snapshot, executable/configuration context and
complete request. No TTL or file timestamp can authorize reuse of old evidence.
The adjacent execution lock is shared with the bounded background job worker.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager, ExitStack
from datetime import date, datetime
import fcntl
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
import types
from zoneinfo import ZoneInfo

from . import catalogs, runtime

MAX_CACHE_ROWS = 16
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024
CONTRACT = "openhealthatlas-exact-read-cache-v1"


class AnalysisBusy(RuntimeError):
    """Another bounded analysis owns the shared execution slot."""


class Uncacheable(ValueError):
    """An executable context cannot be represented without guessing."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(value):
    return "sha256:" + hashlib.sha256(_json(value).encode()).hexdigest()


def _value(value, seen=None):
    """Typed, deterministic private configuration; never returned to clients."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise Uncacheable("Non-finite context")
        return {"float": value.hex()}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, (date, datetime)):
        return {type(value).__name__: value.isoformat()}
    if isinstance(value, ZoneInfo):
        return {"timezone": value.key}
    if value is Ellipsis:
        return {"ellipsis": True}
    if isinstance(value, complex):
        return {"complex": [value.real.hex(), value.imag.hex()]}
    seen = set() if seen is None else seen
    if id(value) in seen:
        raise Uncacheable("Recursive context")
    seen = seen | {id(value)}
    if isinstance(value, Mapping):
        pairs = [(_value(k, seen), _value(v, seen)) for k, v in value.items()]
        return {"mapping": sorted(pairs, key=lambda pair: _json(pair[0]))}
    if isinstance(value, (list, tuple)):
        return {type(value).__name__: [_value(v, seen) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {"set": sorted((_value(v, seen) for v in value), key=_json)}
    if isinstance(value, functools.partial):
        return {"partial": _value(value.func, seen),
                "args": _value(value.args, seen),
                "keywords": _value(value.keywords, seen)}
    if isinstance(value, types.CodeType):
        # marshal includes interpreter interning/reference details which can
        # change after a function runs. These executable fields are stable.
        return {"bytecode": value.co_code.hex(), "constants": _value(value.co_consts, seen),
                "names": value.co_names, "variables": value.co_varnames,
                "free": value.co_freevars, "cells": value.co_cellvars,
                "arguments": [value.co_argcount, value.co_posonlyargcount, value.co_kwonlyargcount],
                "flags": value.co_flags}
    if isinstance(value, types.FunctionType):
        return {"function": [value.__module__, value.__qualname__],
                "code": _value(value.__code__, seen),
                "defaults": _value(value.__defaults__, seen),
                "keyword_defaults": _value(value.__kwdefaults__, seen),
                "closure": [_value(c.cell_contents, seen) for c in value.__closure__ or ()]}
    if isinstance(value, (type, types.BuiltinFunctionType)):
        return {"builtin": [value.__module__, value.__qualname__]}
    if hasattr(value, "to_dict"):
        return {"type": [type(value).__module__, type(value).__qualname__],
                "value": _value(value.to_dict(), seen)}
    raise Uncacheable("Unsupported context value")


def cache_root(database):
    """Resolve a server-controlled path; request payloads cannot choose it."""
    database = Path(database).expanduser().resolve(strict=True)
    configured = os.environ.get("OPENHEALTHATLAS_ANALYSIS_DIR")
    parent = Path(configured) if configured else database.parent / ".openhealthatlas-analysis"
    if not parent.is_absolute():
        raise Uncacheable("Analysis directory must be absolute")
    return parent / hashlib.sha256(os.fsencode(database)).hexdigest()[:16]


def store_path(database):
    return cache_root(database) / "analysis.sqlite3"


def _private_directory(path):
    if not path.exists():
        # Only these two application-owned levels may be created.
        if not path.parent.exists():
            path.parent.mkdir(mode=0o700, exist_ok=True)
        path.mkdir(mode=0o700, exist_ok=True)
    for directory in (path.parent, path):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise PermissionError("Analysis storage must be private and owned by this account")


def _private_file(path):
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise PermissionError("Analysis file must be private and owned by this account")


def connect_store(database):
    path = store_path(database)
    _private_directory(path.parent)
    _private_file(path)
    for suffix in ("-wal", "-shm", "-journal"):
        companion = Path(str(path) + suffix)
        if companion.exists() or companion.is_symlink():
            _private_file(companion)
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS exact_results (
            cache_key TEXT PRIMARY KEY, identity TEXT NOT NULL,
            request_json TEXT NOT NULL, result_json TEXT NOT NULL,
            result_sha256 TEXT NOT NULL, created_at REAL NOT NULL,
            accessed_at REAL NOT NULL, result_bytes INTEGER NOT NULL
        )""")
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def computation_lock(database, *, wait=False):
    path = cache_root(database) / "execution.lock"
    _private_directory(path.parent)
    _private_file(path)
    inherited = os.environ.get("OHA_ANALYSIS_LOCK_FD")
    if inherited is not None:
        try:
            descriptor = int(inherited)
            actual, expected = os.fstat(descriptor), path.stat()
            if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                    or actual.st_mode & 0o077 or (actual.st_dev, actual.st_ino, actual.st_uid) != (
                expected.st_dev, expected.st_ino, os.geteuid()
            )):
                raise ValueError("Wrong inherited lock")
            # Acquiring again on the SAME open file description is harmless;
            # a separately opened descriptor cannot bypass another holder.
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ValueError):
            raise AnalysisBusy("Invalid inherited analysis execution slot") from None
        yield descriptor
        return  # The worker and child jointly own it; never unlock it here.
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise AnalysisBusy("An analysis is already running; use its job status") from None
        yield descriptor
    finally:
        os.close(descriptor)


def _row_hash(row):
    encoded = _json([_value(value) for value in row]).encode()
    return hashlib.sha256(encoded).digest()


def _database_identity(connection):
    if not connection.in_transaction:
        raise Uncacheable("A coherent read transaction is required")
    schema = list(connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ))
    digest = hashlib.sha256()
    digest.update(_json([list(row) for row in schema]).encode())
    for kind, name, _table, _sql in schema:
        if kind != "table":
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        columns = {row[1].casefold() for row in connection.execute("PRAGMA table_info(" + quoted + ")")}
        hidden = next((candidate for candidate in ("_rowid_", "rowid", "oid")
                       if candidate not in columns), None)
        # Evidence can identify rows by SQLite's implicit rowid. Replacing an
        # otherwise identical row must invalidate its old source reference.
        selection = (hidden + ", *") if hidden else "*"
        try:
            cursor = connection.execute("SELECT " + selection + " FROM " + quoted)
        except sqlite3.OperationalError as exc:
            if hidden is None or str(exc) != "no such column: " + hidden:
                raise
            cursor = connection.execute("SELECT * FROM " + quoted)  # WITHOUT ROWID
        rows = sorted(_row_hash(row) for row in cursor)
        digest.update(_json([name, len(rows)]).encode())
        for value in rows:
            digest.update(value)
    return digest.hexdigest()


def current_identity(database, *, connection=None, context=None):
    own = connection is None
    if own:
        connection = runtime.connect_read_only(str(database))
        connection.execute("BEGIN")
    try:
        context = context or runtime.adapter_context()
        toolkit = Path(__file__).resolve().parent.parent
        manifest = [(str(path.relative_to(toolkit)), hashlib.sha256(path.read_bytes()).hexdigest())
                    for path in sorted(toolkit.rglob("*.py"))
                    if "__pycache__" not in path.parts and "tests" not in path.relative_to(toolkit).parts]
        manifest.append(("SCHEMA.sql", hashlib.sha256((toolkit / "SCHEMA.sql").read_bytes()).hexdigest()))
        native_source = toolkit / "hermes_insights/native/rank_products.c"
        manifest.append(("hermes_insights/native/rank_products.c",
                         hashlib.sha256(native_source.read_bytes()).hexdigest()))
        effective = {name: value for name, value in vars(catalogs).items() if name.isupper()}
        return _hash({"contract": CONTRACT, "database": str(Path(database).resolve()),
                      "rows": _database_identity(connection), "code": manifest,
                      "python": sys.version, "today": context.today.isoformat(),
                      "timezone": context.timezone, "constants": _value(context.constants),
                      "functions": _value(context.functions), "catalogs": _value(effective)})
    finally:
        if own:
            connection.close()


def _read(connection, key, identity, request_json):
    row = connection.execute("SELECT * FROM exact_results WHERE cache_key=?", (key,)).fetchone()
    if row is None:
        return None
    if not isinstance(row["result_json"], str):
        connection.execute("DELETE FROM exact_results WHERE cache_key=?", (key,))
        return None
    raw = row["result_json"].encode()
    valid = (row["identity"] == identity and row["request_json"] == request_json
             and len(raw) <= MAX_RESULT_BYTES and len(raw) == row["result_bytes"]
             and hashlib.sha256(raw).hexdigest() == row["result_sha256"])
    try:
        result = json.loads(raw) if valid else None
        valid = valid and isinstance(result, dict) and _json(result).encode() == raw
    except (ValueError, TypeError):
        valid = False
    if not valid:
        connection.execute("DELETE FROM exact_results WHERE cache_key=?", (key,))
        return None
    connection.execute("UPDATE exact_results SET accessed_at=? WHERE cache_key=?", (time.time(), key))
    return result


def _write(connection, key, identity, request_json, result):
    raw = _json(result).encode()
    if len(raw) > MAX_RESULT_BYTES:
        return
    now = time.time()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("INSERT OR REPLACE INTO exact_results VALUES(?,?,?,?,?,?,?,?)",
                           (key, identity, request_json, raw.decode(), hashlib.sha256(raw).hexdigest(),
                            now, now, len(raw)))
        rows = connection.execute("SELECT cache_key,result_bytes FROM exact_results ORDER BY accessed_at DESC,cache_key").fetchall()
        total = 0
        for index, row in enumerate(rows):
            total += row["result_bytes"]
            if index >= MAX_CACHE_ROWS or total > MAX_CACHE_BYTES:
                connection.execute("DELETE FROM exact_results WHERE cache_key=?", (row["cache_key"],))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _uncached_compute(database, compute):
    # Optional persistence failure must not permit overlapping calculations.
    # Existing immutable/read-only installations retain their stateless path
    # only when no private execution workspace can be opened at all.
    stack = ExitStack()
    try:
        stack.enter_context(computation_lock(database))
    except (PermissionError, FileNotFoundError):
        stack.close()
        return compute()
    except OSError as exc:
        import errno
        stack.close()
        if exc.errno == errno.EROFS:
            return compute()
        raise
    with stack:
        return compute()


def cached_compute(database, request, compute, *, connection, context, definitions=None):
    """Reuse only an exact read; a broken/unavailable cache is never evidence."""
    if os.environ.get("OPENHEALTHATLAS_DISABLE_ANALYSIS_CACHE") == "1":
        return _uncached_compute(database, compute)
    store = None
    try:
        identity = current_identity(database, connection=connection, context=context)
        request_json = _json({"request": _value(request), "definitions": _value(definitions)})
        key = _hash([identity, request_json])
        store = connect_store(database)
        cached = _read(store, key, identity, request_json)
    except (OSError, sqlite3.Error, Uncacheable, ValueError, TypeError):
        if store is not None:
            store.close()
        return _uncached_compute(database, compute)
    try:
        if cached is not None:
            return cached
        with computation_lock(database):
            cached = _read(store, key, identity, request_json)
            if cached is not None:
                return cached
            result = compute()
            try:
                _write(store, key, identity, request_json, result)
            except (OSError, sqlite3.Error, ValueError, TypeError):
                pass  # Computed evidence remains valid; persistence is optional.
            return result
    finally:
        store.close()


def refresh_readiness(database, result, *, connection=None, context=None, definitions=None):
    """Refresh clock-sensitive collection status without repeating statistics.

Outcome findings depend on the snapshot and civil day. The readiness envelope
also reports collector ages in hours, so it is re-evaluated whenever a cached
or saved outcome is served. Generic Hermes projects no readiness envelope.
"""
    if not isinstance(result, dict) or "readiness" not in result:
        return result
    from .contracts import DateRange
    from .readiness import build_readiness
    from .registry import build_registry
    meta = result["meta"]
    requested = meta["requested_range"]
    window = DateRange(
        start=date.fromisoformat(requested["from"]) if requested.get("from") else None,
        end=date.fromisoformat(requested["to"]) if requested.get("to") else None,
        kind=requested["kind"],
    )
    own = connection is None
    if own:
        connection = runtime.connect_read_only(str(database))
        connection.execute("BEGIN")
    try:
        context = context or runtime.adapter_context()
        definitions = definitions if definitions is not None else build_registry(connection, context)
        result = dict(result)
        result["readiness"] = build_readiness(
            connection, definitions, window, context, outcome=meta["outcome"],
        )
        return result
    finally:
        if own:
            connection.close()

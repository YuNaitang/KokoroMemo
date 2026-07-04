# Task 8: Add `get_vector_store()` to services.py

## Status
Completed.

## Commits
No commits — changes are uncommitted:
- Modified: `app/core/services.py`

## Changes
Two additions to `app/core/services.py`:

1. **Import**: Added `from app.storage.database import is_server_mode` at line 15.
2. **New function `get_vector_store(cfg)`** at line 186:
   - Returns `None` when `cfg.embedding.enabled` is `False`
   - In server mode (`is_server_mode()` returns `True`): returns a `PgVectorStore` initialized with a session factory from `get_engine()` and `async_sessionmaker`, plus `dimension` from config
   - In file mode: returns a `LanceDBVectorStore` initialized with the path from `resolve_lancedb_path(cfg)`, table name from `cfg.storage.lancedb.table`, and dimension from config
   - Both store classes are imported lazily inside their respective branches to keep module-level imports clean

## Constraints satisfied
- `get_lancedb_store()` is completely untouched (lines 150-183 are unchanged)
- `resolve_lancedb_path()` is reused for the LanceDB case
- No existing function signatures or behavior modified

## Concerns
- `get_vector_store()` does **not** call `.connect()` on the returned store, unlike `get_lancedb_store()` which auto-connects. The caller is responsible for calling `.connect()` before use. This is intentional — lifecycle management differs between the two code paths (server mode's PgVectorStore needs async context, and the caller may need to control timing).
- The PgVectorStore path creates a new `async_sessionmaker` on every call. If called frequently, this creates unnecessary objects — caching could be added later if profiling shows a need.

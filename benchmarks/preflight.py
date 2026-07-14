#!/usr/bin/env python3
"""Standalone preflight runner.

Delegates to memoryhub_evalhub.preflight. Requires the evalhub-adapter
package on the Python path.

Usage:
    python benchmarks/preflight.py [--config path/to/config.yaml]

Environment:
    MEMORYHUB_DB_HOST, MEMORYHUB_DB_PORT, MEMORYHUB_DB_USER,
    MEMORYHUB_DB_PASS, MEMORYHUB_DB_NAME -- database connection
    MEMORYHUB_RERANKER_URL -- reranker endpoint (optional)
    MEMORYHUB_TENANT_ID -- target tenant (default: amb-benchmark)
"""

import sys
from pathlib import Path

# Add the adapter package to the path so the import works from repo root
_adapter_src = Path(__file__).resolve().parent / "evalhub-adapter" / "src"
if str(_adapter_src) not in sys.path:
    sys.path.insert(0, str(_adapter_src))

from memoryhub_evalhub.preflight import main  # noqa: E402

if __name__ == "__main__":
    main()

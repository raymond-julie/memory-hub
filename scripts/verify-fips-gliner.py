#!/usr/bin/env python3
"""Verify GLiNER2 compatibility with FIPS-enabled environments (#265).

Checks that model loading and entity inference do not trigger FIPS
violations (restricted hash functions like MD5/SHA1 used for security
purposes). Designed to run inside a UBI9 container on a FIPS-enabled
OpenShift cluster.

Static analysis (2026-06-09) found:
  - GLiNER inference path: no crypto, FIPS-clean.
  - safetensors / PyTorch deserialization: no crypto, FIPS-clean.
  - huggingface_hub download path: one raw hashlib.sha1() call in
    _local_folder.py:473 (_short_hash) without usedforsecurity=False.
    Only hit during active downloads, not when loading from cache.
  - huggingface_hub SHA-256 verification: uses usedforsecurity=False,
    FIPS-safe.

This script confirms the static findings at runtime by exercising the
model-load and inference paths and catching any ValueError raised by
hashlib in FIPS mode.

Usage:
    # Inside a FIPS-enabled container (e.g., via the K8s Job manifest):
    python scripts/verify-fips-gliner.py

    # Locally (non-FIPS) -- reports FIPS mode inactive, still runs tests:
    python scripts/verify-fips-gliner.py
"""

from __future__ import annotations

import os
import sys
import time
import traceback


def check_fips_mode() -> bool:
    """Return True if the kernel has FIPS mode enabled."""
    try:
        with open("/proc/sys/crypto/fips_enabled") as f:
            return f.read().strip() == "1"
    except FileNotFoundError:
        return False


def verify_model_load(model_name: str) -> tuple[bool, str, float]:
    """Load GLiNER model and return (success, message, elapsed_seconds)."""
    from gliner import GLiNER

    start = time.monotonic()
    try:
        GLiNER.from_pretrained(model_name)
        elapsed = time.monotonic() - start
        return True, f"Model loaded successfully in {elapsed:.1f}s", elapsed
    except ValueError as exc:
        elapsed = time.monotonic() - start
        if "usedforsecurity" in str(exc).lower() or "fips" in str(exc).lower():
            return False, f"FIPS violation during model load: {exc}", elapsed
        return False, f"Unexpected ValueError: {exc}", elapsed
    except Exception as exc:
        elapsed = time.monotonic() - start
        return False, f"Error during model load: {exc}", elapsed


def verify_inference(model_name: str) -> tuple[bool, str, float]:
    """Run entity prediction and return (success, message, elapsed_seconds)."""
    from gliner import GLiNER

    model = GLiNER.from_pretrained(model_name)
    labels = [
        "person", "organization", "location", "event",
        "technology", "programming language", "framework",
    ]
    text = (
        "Wes Jackson deployed MemoryHub on Red Hat OpenShift using Python "
        "and FastAPI. The service runs PostgreSQL with pgvector for vector "
        "search in the memoryhub namespace."
    )

    start = time.monotonic()
    try:
        entities = model.predict_entities(text, labels, threshold=0.5)
        elapsed = time.monotonic() - start
        entity_summary = ", ".join(
            f"{e['text']}({e['label']})" for e in entities
        )
        return (
            True,
            f"Inference OK in {elapsed:.1f}s: {entity_summary or 'no entities'}",
            elapsed,
        )
    except ValueError as exc:
        elapsed = time.monotonic() - start
        if "usedforsecurity" in str(exc).lower() or "fips" in str(exc).lower():
            return False, f"FIPS violation during inference: {exc}", elapsed
        return False, f"Unexpected ValueError: {exc}", elapsed
    except Exception as exc:
        elapsed = time.monotonic() - start
        return False, f"Error during inference: {exc}", elapsed


def verify_hashlib_fips_awareness() -> tuple[bool, str]:
    """Check that huggingface_hub's insecure_hashlib wrapper is present."""
    try:
        from huggingface_hub.utils import insecure_hashlib
        insecure_hashlib.sha256()
        return True, "huggingface_hub insecure_hashlib wrapper present and functional"
    except ImportError:
        return False, "huggingface_hub insecure_hashlib wrapper NOT found"
    except Exception as exc:
        return False, f"insecure_hashlib check failed: {exc}"


def main() -> int:
    model_name = os.environ.get(
        "MEMORYHUB_GLINER_MODEL", "urchade/gliner_medium-v2.1"
    )

    fips_active = check_fips_mode()
    print(f"FIPS mode: {'ACTIVE' if fips_active else 'inactive'}")  # noqa: T201
    print(f"Model: {model_name}")  # noqa: T201
    print(f"Python: {sys.version}")  # noqa: T201
    print()  # noqa: T201

    if not fips_active:
        print(  # noqa: T201
            "WARNING: FIPS mode is not active on this system. Tests will "
            "still run but cannot confirm FIPS compliance. Run this script "
            "on a FIPS-enabled cluster for authoritative results."
        )
        print()  # noqa: T201

    results: list[tuple[str, bool, str]] = []

    # Test 1: hashlib wrapper
    ok, msg = verify_hashlib_fips_awareness()
    results.append(("hashlib wrapper", ok, msg))
    print(f"[{'PASS' if ok else 'FAIL'}] hashlib wrapper: {msg}")  # noqa: T201

    # Test 2: model load
    ok, msg, _ = verify_model_load(model_name)
    results.append(("model load", ok, msg))
    print(f"[{'PASS' if ok else 'FAIL'}] model load: {msg}")  # noqa: T201

    # Test 3: inference
    if results[-1][1]:
        ok, msg, _ = verify_inference(model_name)
        results.append(("inference", ok, msg))
        print(f"[{'PASS' if ok else 'FAIL'}] inference: {msg}")  # noqa: T201
    else:
        results.append(("inference", False, "skipped (model load failed)"))
        print("[SKIP] inference: model load failed")  # noqa: T201

    print()  # noqa: T201
    failures = [r for r in results if not r[1]]
    if failures:
        print(f"RESULT: {len(failures)} failure(s)")  # noqa: T201
        for name, _, msg in failures:
            print(f"  - {name}: {msg}")  # noqa: T201
        return 1

    label = "FIPS-VERIFIED" if fips_active else "PASS (non-FIPS)"
    print(f"RESULT: {label} -- all checks passed")  # noqa: T201
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)

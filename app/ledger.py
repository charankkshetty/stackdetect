"""Credit safety for paid APIs: gate + on-disk cache + hard stop.

The SINGLE place any paid provider (Apollo, Parallel) may be called from, so
credit safety cannot leak into individual detectors.

Guarantees:
- CACHE: every paid result is persisted under CACHE_DIR keyed by
  {provider}:{domain}. A cache hit returns the stored result and spends 0
  credits, so re-scanning a domain is free.
- CAP: per-provider spend is persisted to CACHE_DIR/ledger.json with a HARD CAP
  of 300 credits each. At the cap, spend() returns "cap reached" — it does not
  increment, does not invoke the provider, and does not raise.
- GATE: an optional stack_score refuses to enrich a company that scored 0 from
  the free detectors (see CLAUDE.md).

VOLUME SAFETY: the storage root comes from env var CACHE_DIR, defaulting to
./cache for local runs. On Railway, CACHE_DIR points at a mounted persistent
volume (/data) so the ledger and cache survive redeploys — Railway's ordinary
filesystem is ephemeral and would silently reset the cap counter on every
deploy. No cache path is hardcoded anywhere else in the codebase.

Counts are re-read from disk on every operation rather than held in memory, so
a restart can never resurrect a stale counter.

FREE detectors (ct_logs, dns, fingerprint, careers) must never import or touch
this module — they cost nothing and are not rate-limited by a credit budget.

Self-test: python -m app.ledger
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

# Providers that cost money. Anything else is rejected, which structurally
# prevents a free detector from ever registering spend.
PROVIDERS = ("apollo", "parallel")

HARD_CAP = 300

# spend() outcome codes.
OK = "ok"
CACHE_HIT = "cache_hit"
CAP_REACHED = "cap reached"
GATE_BLOCKED = "gate_blocked"

_LEDGER_FILE = "ledger.json"
_RESULTS_DIR = "results"

# Serialises the read-modify-write of the ledger file within a process.
_LOCK = threading.Lock()

# A conservative hostname shape. Domains never contain path separators, so
# validating here removes any path-traversal risk from cache filenames.
_DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)


@dataclass
class SpendResult:
    """Outcome of an attempted paid call."""

    status: str  # OK | CACHE_HIT | CAP_REACHED | GATE_BLOCKED
    provider: str
    domain: str
    result: Any = None
    credits_spent: int = 0

    @property
    def allowed(self) -> bool:
        """True when the caller got data, whether fresh or from cache."""
        return self.status in (OK, CACHE_HIT)


# --------------------------------------------------------------------------
# Storage locations — all derived from CACHE_DIR, read at call time so the
# environment can change (tests, a Railway redeploy) without a reimport.
# --------------------------------------------------------------------------


def cache_root() -> Path:
    """Root for the ledger and result cache. CACHE_DIR, else ./cache."""
    return Path(os.environ.get("CACHE_DIR") or "cache")


def ledger_path() -> Path:
    return cache_root() / _LEDGER_FILE


def result_path(provider: str, domain: str) -> Path:
    """On-disk location for one cached {provider}:{domain} result."""
    return cache_root() / _RESULTS_DIR / provider / f"{normalise_domain(domain)}.json"


def normalise_domain(domain: str) -> str:
    d = (domain or "").strip().lower().rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    if not _DOMAIN_RE.match(d):
        raise ValueError(f"not a valid domain: {domain!r}")
    return d


def _check_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError(
            f"unknown paid provider {provider!r}; expected one of {PROVIDERS}"
        )
    return provider


# --------------------------------------------------------------------------
# JSON helpers — atomic writes so a crash or redeploy mid-write cannot leave a
# truncated ledger behind.
# --------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Counters
# --------------------------------------------------------------------------


def _load_counts() -> dict:
    data = _read_json(ledger_path())
    if not isinstance(data, dict):
        data = {}
    counts = {}
    for provider in PROVIDERS:
        value = data.get(provider)
        counts[provider] = value if isinstance(value, int) and value >= 0 else 0
    return counts


def usage() -> dict:
    """Current usage, shaped for the API response and the UI."""
    counts = _load_counts()
    return {
        "apollo_used": counts["apollo"],
        "apollo_cap": HARD_CAP,
        "parallel_used": counts["parallel"],
        "parallel_cap": HARD_CAP,
    }


def remaining(provider: str) -> int:
    _check_provider(provider)
    return max(0, HARD_CAP - _load_counts()[provider])


def at_cap(provider: str) -> bool:
    return remaining(provider) <= 0


def _increment(provider: str, amount: int = 1) -> int:
    """Re-read, add, write back under the lock. Returns the new count."""
    with _LOCK:
        counts = _load_counts()
        counts[provider] = counts[provider] + amount
        _write_json(ledger_path(), counts)
        return counts[provider]


# --------------------------------------------------------------------------
# Result cache
# --------------------------------------------------------------------------


def cached_result(provider: str, domain: str):
    """Stored result for {provider}:{domain}, or None on a miss."""
    _check_provider(provider)
    entry = _read_json(result_path(provider, domain))
    if isinstance(entry, dict) and "result" in entry:
        return entry["result"]
    return None


def store_result(provider: str, domain: str, result: Any) -> None:
    _check_provider(provider)
    _write_json(
        result_path(provider, domain),
        {"provider": provider, "domain": normalise_domain(domain), "result": result},
    )


# --------------------------------------------------------------------------
# The one entry point paid detectors may use
# --------------------------------------------------------------------------


async def spend(
    provider: str,
    domain: str,
    call: Callable[[], Awaitable[Any]],
    *,
    stack_score: int | None = None,
) -> SpendResult:
    """Run a paid call under cache, gate and cap. Never raises on cap.

    `call` is an async zero-arg callable that performs the actual API request.
    It is invoked ONLY when the cache misses, the gate passes and the provider
    is below its cap.

    Order: cache first (a cached domain stays free even at the cap), then the
    gate, then the cap. The counter is incremented and the result cached only
    after `call` returns successfully — a failed call spends nothing. An
    exception from `call` propagates to the caller unchanged.
    """
    _check_provider(provider)
    domain = normalise_domain(domain)

    hit = cached_result(provider, domain)
    if hit is not None:
        return SpendResult(CACHE_HIT, provider, domain, result=hit, credits_spent=0)

    # Gate: never enrich a company with no detected stack (CLAUDE.md).
    if stack_score is not None and stack_score <= 0:
        return SpendResult(GATE_BLOCKED, provider, domain, credits_spent=0)

    if at_cap(provider):
        return SpendResult(CAP_REACHED, provider, domain, credits_spent=0)

    result = await call()

    store_result(provider, domain, result)
    _increment(provider)
    return SpendResult(OK, provider, domain, result=result, credits_spent=1)


# --------------------------------------------------------------------------
# Self-test: python -m app.ledger
# --------------------------------------------------------------------------


def _self_test() -> int:
    import asyncio
    import shutil
    import tempfile

    passed, failed = [], []

    def check(name: str, condition: bool, detail: str = "") -> None:
        (passed if condition else failed).append(name)
        mark = "PASS" if condition else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not condition else ""))

    async def run() -> None:
        calls = {"n": 0}

        async def fake_api():
            calls["n"] += 1
            return {"employees": 250}

        print("\n1. CACHE_DIR wiring")
        check(
            "defaults to ./cache when CACHE_DIR unset",
            cache_root() == Path("cache"),
            f"got {cache_root()}",
        )
        os.environ["CACHE_DIR"] = tmp
        check(
            "follows CACHE_DIR when set",
            cache_root() == Path(tmp),
            f"got {cache_root()}",
        )
        check(
            "ledger lives under CACHE_DIR",
            str(ledger_path()).startswith(tmp),
            f"got {ledger_path()}",
        )

        print("\n2. A fresh call increments the counter")
        r1 = await spend("apollo", "acme.com", fake_api)
        check("status ok", r1.status == OK, f"got {r1.status}")
        check("result returned", r1.result == {"employees": 250})
        check("1 credit spent", r1.credits_spent == 1, f"got {r1.credits_spent}")
        check("apollo_used == 1", usage()["apollo_used"] == 1, str(usage()))
        check("provider was called once", calls["n"] == 1, f"got {calls['n']}")

        print("\n3. A repeat domain hits cache and stays flat")
        r2 = await spend("apollo", "acme.com", fake_api)
        check("status cache_hit", r2.status == CACHE_HIT, f"got {r2.status}")
        check("same result returned", r2.result == {"employees": 250})
        check("0 credits spent", r2.credits_spent == 0, f"got {r2.credits_spent}")
        check("apollo_used still 1", usage()["apollo_used"] == 1, str(usage()))
        check("provider NOT called again", calls["n"] == 1, f"got {calls['n']}")
        check("www./case variants hit the same entry",
              (await spend("apollo", "WWW.Acme.com", fake_api)).status == CACHE_HIT)

        print("\n4. Counts survive a restart (re-read from disk)")
        check("apollo_used persisted", _load_counts()["apollo"] == 1, str(_load_counts()))
        check("ledger.json exists on disk", ledger_path().exists())

        print("\n5. Providers are independent")
        check("parallel_used still 0", usage()["parallel_used"] == 0, str(usage()))
        rp = await spend("parallel", "acme.com", fake_api)
        check("parallel spends its own credit", rp.status == OK and usage()["parallel_used"] == 1)
        check("apollo unaffected", usage()["apollo_used"] == 1, str(usage()))

        print("\n6. Hitting 300 returns 'cap reached'")
        _write_json(ledger_path(), {"apollo": 299, "parallel": 1})
        r299 = await spend("apollo", "underthecap.com", fake_api)
        check("299 -> 300 still allowed", r299.status == OK, f"got {r299.status}")
        check("apollo_used == 300", usage()["apollo_used"] == HARD_CAP, str(usage()))
        calls_before = calls["n"]
        r300 = await spend("apollo", "overthecap.com", fake_api)
        check("returns 'cap reached'", r300.status == CAP_REACHED, f"got {r300.status}")
        check("literal string is 'cap reached'", r300.status == "cap reached")
        check("did NOT increment", usage()["apollo_used"] == HARD_CAP, str(usage()))
        check("did NOT call the API", calls["n"] == calls_before, f"got {calls['n']}")
        check("did NOT raise", True)
        check("cap does not block a CACHED domain",
              (await spend("apollo", "acme.com", fake_api)).status == CACHE_HIT)
        check("parallel unaffected by apollo's cap", not at_cap("parallel"))

        print("\n7. Gate refuses a company with no detected stack")
        rg = await spend("parallel", "nostack.com", fake_api, stack_score=0)
        check("status gate_blocked", rg.status == GATE_BLOCKED, f"got {rg.status}")
        check("0 credits spent", rg.credits_spent == 0)
        check("stack_score>0 passes the gate",
              (await spend("parallel", "hasstack.com", fake_api, stack_score=3)).status == OK)

        print("\n8. Guard rails")
        try:
            await spend("crt_sh", "acme.com", fake_api)
            check("free detector rejected as a provider", False, "no error raised")
        except ValueError:
            check("free detector rejected as a provider", True)
        try:
            normalise_domain("../../etc/passwd")
            check("path traversal rejected", False, "no error raised")
        except ValueError:
            check("path traversal rejected", True)

        print("\n9. A failed call spends nothing")
        before = usage()["parallel_used"]

        async def boom():
            raise RuntimeError("provider 500")

        try:
            await spend("parallel", "broken.com", boom)
        except RuntimeError:
            pass
        check("counter unchanged after failure", usage()["parallel_used"] == before)
        check("failure not cached", cached_result("parallel", "broken.com") is None)

    tmp = tempfile.mkdtemp(prefix="stackdetect-ledger-test-")
    saved = os.environ.pop("CACHE_DIR", None)
    try:
        print(f"ledger self-test (temp CACHE_DIR={tmp})")
        asyncio.run(run())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CACHE_DIR", None)
        if saved is not None:
            os.environ["CACHE_DIR"] = saved

    total = len(passed) + len(failed)
    print(f"\n{'-' * 52}")
    if failed:
        print(f"FAILED — {len(failed)}/{total} checks failed:")
        for name in failed:
            print(f"  - {name}")
        return 1
    print(f"ALL PASS — {total}/{total} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())

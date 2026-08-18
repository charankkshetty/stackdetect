"""Runs every detector for ONE domain and assembles the verdict.

Fans the three FREE detectors out concurrently over a shared httpx client,
then hands their evidence to scoring.py. Paid enrichment is not wired in yet;
when it is, it goes through app.ledger and only for a domain that already
scored stack > 0.

FAILURE ISOLATION is the point of this module. Detectors read third-party
infrastructure that breaks constantly — crt.sh has been down throughout this
build. One detector failing must never fail a scan: each is caught
individually, its status is recorded in signals_summary, and the verdict is
built from whatever the others returned. A caller can always tell a real COLD
from a COLD produced by a broken detector.
"""

from __future__ import annotations

import asyncio

import httpx

from app import scoring
from app.detectors import careers, ct_logs, dns
from app.patterns import is_vendor_domain

STATUS_ERROR = "error"
LEVEL_EXCLUDED = "EXCLUDED"

# The client default matches ct_logs' intended timeout; dns and careers set
# their own shorter timeouts per request.
_CLIENT_TIMEOUT = ct_logs.TIMEOUT_SECONDS
_HEADERS = {"User-Agent": "stackdetect/0.1 (+https://github.com/charankkshetty/stackdetect)"}

_CT_FALLBACK = {"ct_status": STATUS_ERROR, "tools": [], "hostnames_found": 0}
_DNS_FALLBACK = {
    "dns_status": STATUS_ERROR, "tools": [], "wildcard_dns": False,
    "hosts_checked": 0, "hosts_resolved": 0, "hosts_existing": 0, "hosts_unavailable": 0,
}
_CAREERS_FALLBACK = {
    "careers_status": STATUS_ERROR, "tools": [], "triggers": [],
    "sources_fetched": 0, "job_pages_fetched": 0, "job_pages_capped": False,
    "hiring_data_roles": False, "data_role_count": 0, "data_roles": [],
}


def _settle(result, fallback: dict) -> dict:
    """Turn a gather() slot into a usable result dict.

    asyncio.gather(return_exceptions=True) hands back the exception object
    rather than raising it; a detector that blew up becomes status "error"
    instead of taking the scan down with it.
    """
    if isinstance(result, BaseException):
        return dict(fallback)
    return result if isinstance(result, dict) else dict(fallback)


def excluded_verdict(domain: str) -> dict:
    """A vendor's own domain: not a prospect, and no work was done."""
    return {
        "domain": domain,
        "tools": [],
        "trigger_level": LEVEL_EXCLUDED,
        "trigger_evidence": [],
        "self_hosted_orchestrator": False,
        "signals_summary": {
            "ct_logs": "vendor_domain_excluded",
            "dns": "vendor_domain_excluded",
            "careers": "vendor_domain_excluded",
            "data_role_count": 0,
        },
        "fit": None,
    }


def _api_tool(tool: dict) -> dict:
    """One stack entry, trimmed to what the UI renders."""
    return {
        "tool": tool["tool"],
        "confidence": tool["confidence"],
        "signals": tool["signals"],
        "evidence": tool["evidence"],
        # The product's headline flag has to be visible per row, not just in
        # aggregate — this is the column a rep sorts on.
        "self_hosted": tool["self_hosted"],
        "self_hosted_orchestrator": tool["self_hosted_orchestrator"],
    }


def to_api_verdict(verdict: dict) -> dict:
    """scoring.py's verdict in the stable shape the frontend consumes."""
    return {
        "domain": verdict["domain"],
        "tools": [_api_tool(t) for t in verdict["tools"]],
        "trigger_level": verdict["trigger_level"],
        "trigger_evidence": verdict["trigger_evidence"],
        "self_hosted_orchestrator": verdict["self_hosted_orchestrator"],
        "signals_summary": verdict["signals"],
        "fit": verdict["fit"],
    }


async def scan(domain: str, client: httpx.AsyncClient | None = None) -> dict:
    """Scan one domain and return the API-shaped verdict. Never raises."""
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain:
        raise ValueError("domain is required")

    if is_vendor_domain(domain):
        return excluded_verdict(domain)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=_CLIENT_TIMEOUT, headers=_HEADERS)

    try:
        # Independent reads — run them together, not one after another.
        results = await asyncio.gather(
            ct_logs.detect(domain, client),
            dns.detect(domain, client),
            careers.detect(domain, client),
            return_exceptions=True,
        )
    finally:
        if owns_client:
            await client.aclose()

    ct = _settle(results[0], _CT_FALLBACK)
    dns_result = _settle(results[1], _DNS_FALLBACK)
    careers_result = _settle(results[2], _CAREERS_FALLBACK)

    verdict = scoring.build_verdict(domain, ct=ct, dns=dns_result, careers=careers_result)
    return to_api_verdict(verdict)


async def scan_many(domains: list[str]) -> list[dict]:
    """Scan several domains over one client, HOT first."""
    async with httpx.AsyncClient(timeout=_CLIENT_TIMEOUT, headers=_HEADERS) as client:
        verdicts = await asyncio.gather(*[scan(d, client) for d in domains])
    ordered = scoring.sort_verdicts([
        {**v, "tools": [{**t, "confirmations": len(t["signals"])} for t in v["tools"]]}
        for v in verdicts
    ])
    return [{k: v for k, v in verdict.items() if k != "tools"} | {
        "tools": [{k: t[k] for k in t if k != "confirmations"} for t in verdict["tools"]]
    } for verdict in ordered]

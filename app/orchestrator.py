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
import tldextract

from app import scoring
from app.detectors import careers, ct_logs, dns
from app.patterns import is_vendor_domain

STATUS_ERROR = "error"
LEVEL_EXCLUDED = "EXCLUDED"

# Batch concurrency. Every domain fans out to three detectors, so this is the
# knob that keeps a 20-domain demo polite to crt.sh, DoH and Greenhouse.
DEFAULT_CONCURRENCY = 5

# The client default matches ct_logs' intended timeout; dns and careers set
# their own shorter timeouts per request.
_CLIENT_TIMEOUT = ct_logs.TIMEOUT_SECONDS
_HEADERS = {"User-Agent": "stackdetect/0.1 (+https://github.com/charankkshetty/stackdetect)"}

# suffix_list_urls=() pins tldextract to its bundled Public Suffix List
# snapshot, so there is no network fetch on first use. cache_dir=None disables
# the disk cache: on an ephemeral container it buys nothing, and an unwritable
# HOME makes tldextract warn on every start.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

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


def normalise_domain_input(raw: str) -> str:
    """Anything a user might paste -> the registrable root domain.

    Accepts full URLs, www prefixes, paths, ports and mixed case:
        https://international.nubank.com.br/careers  -> nubank.com.br
        www.Nubank.com.br                            -> nubank.com.br

    Reducing to the REGISTRABLE ROOT is deliberate, not cosmetic. Careers
    boards and the vanity-subdomain probes hang off the root, so scanning
    international.nubank.com.br would check international.nubank.com.br/careers
    and airflow.international.nubank.com.br and find nothing.

    The root is derived with tldextract rather than by splitting on the last
    two labels, because multi-part suffixes (nubank.com.br, a.co.uk) make that
    split wrong.
    """
    text = (raw or "").strip().lower()
    if not text:
        return ""
    # scheme
    if "//" in text:
        text = text.split("//", 1)[1]
    # credentials, then path / query / fragment
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    for separator in ("/", "?", "#"):
        text = text.split(separator, 1)[0]
    # port
    text = text.split(":", 1)[0].strip().rstrip(".")
    if text.startswith("www."):
        text = text[4:]
    if not text:
        return ""

    extracted = _EXTRACT(text)
    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}"
    # No recognised suffix (localhost, an IP, junk): hand back what we have and
    # let the caller's validator reject it.
    return text


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
        "trigger_basis": None,
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
        "trigger_basis": verdict.get("trigger_basis"),
        "signals_summary": verdict["signals"],
        "fit": verdict["fit"],
    }


async def scan(domain: str, client: httpx.AsyncClient | None = None) -> dict:
    """Scan one domain and return the API-shaped verdict. Never raises."""
    # Defensive: normalise here too, so every caller of scan() — the API, a
    # batch run, a future cron — gets identical input handling.
    domain = normalise_domain_input(domain)
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


async def scan_many(
    domains: list[str],
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[dict]:
    """Scan several domains over one client, HOT first.

    Concurrency is capped because a batch multiplies every detector: 20 domains
    is 20 crt.sh queries, 320 DoH lookups and up to 20 Greenhouse boards. Five
    at a time keeps a demo fast without hammering anyone's infrastructure.

    Input is normalised and deduped first, so a messy pasted list — full URLs,
    www prefixes, the same company twice — scans each company once.
    """
    seen: list[str] = []
    for raw in domains or []:
        normalised = normalise_domain_input(raw)
        if normalised and normalised not in seen:
            seen.append(normalised)
    if not seen:
        return []

    limiter = asyncio.Semaphore(max(1, concurrency))

    async def one(domain: str) -> dict:
        async with limiter:
            return await scan(domain, client)

    async with httpx.AsyncClient(timeout=_CLIENT_TIMEOUT, headers=_HEADERS) as client:
        verdicts = await asyncio.gather(*[one(d) for d in seen])

    return scoring.sort_verdicts(list(verdicts))

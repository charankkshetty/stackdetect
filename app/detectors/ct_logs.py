"""crt.sh certificate-transparency lookup (FREE).

The core differentiator. Every publicly-trusted TLS certificate is logged to
Certificate Transparency, so querying crt.sh for *.<domain> exposes internal
hostnames — airflow.acme.com, superset.acme.com — that never appear in website
page-source. BuiltWith, ZoomInfo and Wappalyzer read page-source, which is why
they are blind to the data layer.

SCOPE: this detector queries the TARGET's own domain, so it applies only the
self-hosted patterns — the crown-jewel signal. A managed vendor's hostname
(acme.snowflakecomputing.com) lives under the VENDOR's domain and can never
appear in a %.{domain} query, so those patterns are excluded here; they are
reached by dns.py (CNAME) or a future inverted CT vendor scan. See
app/patterns.py.

Public read only: one unauthenticated GET to a public log aggregator.

crt.sh is genuinely slow and intermittently returns HTML error pages instead of
JSON. Any failure degrades to ct_status="unavailable" with an empty tool list —
a crt.sh outage must never crash a scan.

Standalone test: python -m app.detectors.ct_logs <domain> [<domain> ...]
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.patterns import CT_PATTERNS, is_vendor_domain

CRT_SH_URL = "https://crt.sh/?q=%25.{domain}&output=json"
TIMEOUT_SECONDS = 8.0

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
# The target is a vendor's own domain: skipped, so we never report the Airflow
# project as an Airflow user. See VENDOR_DOMAINS in app/patterns.py.
STATUS_VENDOR_EXCLUDED = "vendor_domain_excluded"

# crt.sh rejects a request with no User-Agent often enough to be worth setting.
_HEADERS = {"User-Agent": "stackdetect/0.1 (+https://github.com/charankkshetty/stackdetect)"}


def _clean_hostname(raw: str) -> str | None:
    """Normalise one CT identity into a bare lowercase hostname."""
    host = (raw or "").strip().lower().rstrip(".")
    if host.startswith("*."):
        host = host[2:]
    # CT name_value can carry email addresses on some certs; ignore them.
    if not host or "@" in host or " " in host:
        return None
    return host


def extract_hostnames(certs: Any) -> set[str]:
    """Collect every hostname from a crt.sh JSON payload.

    name_value holds one or more identities separated by newlines, and may
    include wildcard entries.
    """
    hostnames: set[str] = set()
    if not isinstance(certs, list):
        return hostnames
    for cert in certs:
        if not isinstance(cert, dict):
            continue
        for raw in str(cert.get("name_value") or "").split("\n"):
            host = _clean_hostname(raw)
            if host:
                hostnames.add(host)
    return hostnames


def _matches(hostname: str, entry: dict) -> bool:
    pattern = entry["pattern"]
    if entry["kind"] == "self_hosted":
        # The label must be a subdomain of the target: airflow.acme.com.
        return hostname.startswith(f"{pattern}.")
    return pattern in hostname


def match_patterns(hostnames: set[str]) -> list[dict]:
    """Apply the vendor patterns, one entry per tool.

    Dedupes so a tool found on several hostnames returns once, keeping the
    highest-confidence hostname as evidence. Ties break on the shortest
    hostname, then alphabetically, so results are stable across scans.
    """
    best: dict[str, dict] = {}
    for hostname in hostnames:
        for entry in CT_PATTERNS:
            if not _matches(hostname, entry):
                continue
            tool = entry["tool"]
            candidate = {
                "tool": tool,
                "signal": "ct_log",
                "confidence": entry["confidence"],
                "evidence": hostname,
                "self_hosted": entry["kind"] == "self_hosted",
                # Only a REAL orchestrator may raise Orchestra's #1 buying
                # signal. Self-hosted BI (Metabase, Superset) is useful stack
                # signal but must never set this.
                "self_hosted_orchestrator": (
                    entry["kind"] == "self_hosted" and entry["orchestrator"]
                ),
            }
            incumbent = best.get(tool)
            if incumbent is None or _rank(candidate) < _rank(incumbent):
                best[tool] = candidate
    return sorted(best.values(), key=lambda t: (-t["confidence"], t["tool"]))


def _rank(tool: dict) -> tuple:
    return (-tool["confidence"], len(tool["evidence"]), tool["evidence"])


async def detect(domain: str, client: httpx.AsyncClient | None = None) -> dict:
    """Look up `domain` in CT logs and return matched tools.

    Returns {"ct_status", "tools", "hostnames_found"}, where each tool carries
    self_hosted and self_hosted_orchestrator. Never raises on a network or
    parse failure.
    """
    domain = domain.strip().lower().rstrip(".")

    # Bail before any network work — a vendor's own domain is not a prospect.
    if is_vendor_domain(domain):
        return {
            "ct_status": STATUS_VENDOR_EXCLUDED,
            "tools": [],
            "hostnames_found": 0,
        }

    url = CRT_SH_URL.format(domain=domain)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=_HEADERS)

    try:
        response = await client.get(url, follow_redirects=True)
        if response.status_code != 200:
            return _unavailable()
        certs = json.loads(response.text)
    except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError):
        # Timeout, connection reset, or crt.sh serving an HTML error page.
        return _unavailable()
    finally:
        if owns_client:
            await client.aclose()

    hostnames = extract_hostnames(certs)
    return {
        "ct_status": STATUS_OK,
        "tools": match_patterns(hostnames),
        "hostnames_found": len(hostnames),
    }


def _unavailable() -> dict:
    return {"ct_status": STATUS_UNAVAILABLE, "tools": [], "hostnames_found": 0}


if __name__ == "__main__":
    import asyncio
    import sys

    async def _main(domains: list[str]) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=_HEADERS) as client:
            for domain in domains:
                print(f"\n{'=' * 62}\n{domain}\n{'=' * 62}")
                result = await detect(domain, client)
                if result["ct_status"] == STATUS_VENDOR_EXCLUDED:
                    print("ct_status: vendor_domain_excluded")
                    print("  vendor's own domain — not a prospect, nothing scanned")
                    continue
                print(
                    f"ct_status: {result['ct_status']}   "
                    f"hostnames seen: {result['hostnames_found']}"
                )
                if not result["tools"]:
                    print("  (no vendor patterns matched)")
                for tool in result["tools"]:
                    if tool["self_hosted_orchestrator"]:
                        flag = "  [SELF-HOSTED ORCHESTRATOR]"
                    elif tool["self_hosted"]:
                        flag = "  [self-hosted]"
                    else:
                        flag = ""
                    print(
                        f"  {tool['tool']:<34} {tool['confidence']:<5} "
                        f"{tool['evidence']}{flag}"
                    )

    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit("usage: python -m app.detectors.ct_logs <domain> [<domain> ...]")
    asyncio.run(_main(argv))

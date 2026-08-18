"""DNS-over-HTTPS CNAME/A lookup (FREE).

Catches MANAGED data-SaaS on the target's own domain: a vanity hostname
CNAME'd at a vendor (bi.acme.com -> acme.looker.com) is hard evidence the
company runs that vendor. This is the managed-vendor coverage that ct_logs.py
cannot see, because a %.{domain} CT query only returns hostnames under the
target's own domain.

Also complements the CT path on self-hosted tools: a live A record on
airflow.acme.com is a second, independent route to the self-hosted-orchestrator
signal.

DNS is a direct lookup, not an enumeration, so we probe a fixed candidate list
of common data-tool vanity subdomains. Public read only: DoH resolvers only, no
connection to the hosts themselves.

WILDCARD DNS: many domains resolve *.domain.com to their marketing site, which
would make every candidate host look "live" and fire all six self-hosted
signals on any such domain. We probe a nonexistent hostname first; if it
resolves, the domain has a wildcard and A-record liveness proves nothing, so
A-based self-hosted signals are suppressed. CNAME evidence, which names a
specific vendor, is still reported.

Resilience: Google DoH primary, Cloudflare fallback, 5s timeout. If both fail
for a host that host is marked unavailable; a resolver outage never raises.

Standalone test: python -m app.detectors.dns <domain> [<domain> ...]
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.patterns import MANAGED_SAAS_PATTERNS, SELF_HOSTED_PATTERNS, is_vendor_domain

GOOGLE_DOH = "https://dns.google/resolve"
CLOUDFLARE_DOH = "https://cloudflare-dns.com/dns-query"
TIMEOUT_SECONDS = 5.0

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
# The target is a vendor's own domain: skipped, so we never report the Airflow
# project as an Airflow user. See VENDOR_DOMAINS in app/patterns.py.
STATUS_VENDOR_EXCLUDED = "vendor_domain_excluded"

# DNS record type numbers as they appear in a DoH Answer.
_TYPE_A = 1
_TYPE_CNAME = 5

# Common data-tool vanity subdomains. A direct lookup needs specific names.
VANITY_SUBDOMAINS = [
    "bi",
    "looker",
    "tableau",
    "metabase",
    "superset",
    "airflow",
    "dagster",
    "prefect",
    "snowflake",
    "data",
    "analytics",
    "warehouse",
    "dbt",
]

# Label used to detect wildcard DNS. Must be something nobody would configure.
_WILDCARD_PROBE_LABEL = "stackdetect-wildcard-probe-zz9"

# Cap concurrent DoH requests so a multi-domain scan stays polite.
_MAX_CONCURRENCY = 8

_HEADERS = {"User-Agent": "stackdetect/0.1 (+https://github.com/charankkshetty/stackdetect)"}


def candidate_hosts(domain: str) -> list[str]:
    """Root, www, and the data-tool vanity subdomains."""
    domain = domain.strip().lower().rstrip(".")
    return [domain, f"www.{domain}"] + [f"{sub}.{domain}" for sub in VANITY_SUBDOMAINS]


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


async def _query_provider(
    client: httpx.AsyncClient, url: str, host: str, rtype: str, headers: dict
) -> dict | None:
    """One DoH request. None means this provider could not answer."""
    try:
        response = await client.get(
            url,
            params={"name": host, "type": rtype},
            headers=headers,
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    # 0 NOERROR and 3 NXDOMAIN are real answers. Anything else (e.g. 2
    # SERVFAIL) means this resolver failed and we should try the fallback.
    if payload.get("Status") not in (0, 3):
        return None
    return payload


async def resolve(client: httpx.AsyncClient, host: str, rtype: str) -> dict | None:
    """Resolve one host/type, Google first then Cloudflare. None if both fail."""
    payload = await _query_provider(client, GOOGLE_DOH, host, rtype, _HEADERS)
    if payload is not None:
        return payload
    return await _query_provider(
        client,
        CLOUDFLARE_DOH,
        host,
        rtype,
        {**_HEADERS, "accept": "application/dns-json"},
    )


def _answers(payload: dict | None, record_type: int) -> list[str]:
    if not payload:
        return []
    values = []
    for answer in payload.get("Answer") or []:
        if isinstance(answer, dict) and answer.get("type") == record_type:
            value = str(answer.get("data") or "").strip().lower().rstrip(".")
            if value:
                values.append(value)
    return values


async def _lookup_host(client: httpx.AsyncClient, host: str, sem: asyncio.Semaphore) -> dict:
    """CNAME + A for one host. A record query also surfaces any CNAME chain."""
    async with sem:
        cname_payload, a_payload = await asyncio.gather(
            resolve(client, host, "CNAME"),
            resolve(client, host, "A"),
        )
    if cname_payload is None and a_payload is None:
        return {"host": host, "status": STATUS_UNAVAILABLE, "cnames": [], "a": []}

    cnames = _answers(cname_payload, _TYPE_CNAME) + _answers(a_payload, _TYPE_CNAME)
    return {
        "host": host,
        "status": STATUS_OK,
        "cnames": sorted(set(cnames)),
        "a": sorted(set(_answers(a_payload, _TYPE_A))),
    }


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def _rank(tool: dict) -> tuple:
    return (-tool["confidence"], len(tool["evidence"]), tool["evidence"])


def _add(best: dict, candidate: dict) -> None:
    incumbent = best.get(candidate["tool"])
    if incumbent is None or _rank(candidate) < _rank(incumbent):
        best[candidate["tool"]] = candidate


def match_records(host_results: list[dict], *, wildcard: bool = False) -> list[dict]:
    """Turn resolved records into evidence, one entry per tool.

    Managed vendors are matched on the CNAME target (substring). Self-hosted
    tools are matched on a live A record for a "<label>." host, and are
    suppressed entirely when the domain has wildcard DNS.
    """
    best: dict[str, dict] = {}

    for record in host_results:
        if record["status"] != STATUS_OK:
            continue
        host = record["host"]

        # Managed SaaS: the CNAME target names the vendor.
        for target in record["cnames"]:
            for entry in MANAGED_SAAS_PATTERNS:
                if entry["pattern"] in target:
                    _add(best, {
                        "tool": entry["tool"],
                        "signal": "dns_cname",
                        "confidence": entry["confidence"],
                        "evidence": f"{host} -> {target}",
                        "self_hosted": False,
                        "self_hosted_orchestrator": False,
                    })

        # Self-hosted: a live A record on airflow.acme.com. Meaningless under
        # wildcard DNS, where every name resolves.
        if wildcard or not record["a"]:
            continue
        for entry in SELF_HOSTED_PATTERNS:
            if host.startswith(f"{entry['pattern']}."):
                _add(best, {
                    "tool": entry["tool"],
                    "signal": "dns_a",
                    "confidence": entry["confidence"],
                    "evidence": f"{host} -> {record['a'][0]}",
                    "self_hosted": True,
                    # Only a real orchestrator raises Orchestra's #1 signal;
                    # self-hosted BI must never set this.
                    "self_hosted_orchestrator": entry["orchestrator"],
                })

    return sorted(best.values(), key=lambda t: (-t["confidence"], t["tool"]))


# --------------------------------------------------------------------------
# Detector entry point
# --------------------------------------------------------------------------


async def detect(domain: str, client: httpx.AsyncClient | None = None) -> dict:
    """Probe `domain`'s candidate hostnames and return matched tools.

    Returns {"dns_status", "tools", "wildcard_dns", "hosts_checked",
    "hosts_resolved", "hosts_existing", "hosts_unavailable"}. Note that
    hosts_resolved counts resolver answers (NXDOMAIN included); hosts_existing
    counts hosts that actually have a record. Never raises on a failure.
    """
    domain = domain.strip().lower().rstrip(".")

    # Bail before any network work — a vendor's own domain is not a prospect.
    if is_vendor_domain(domain):
        return {
            "dns_status": STATUS_VENDOR_EXCLUDED,
            "tools": [],
            "wildcard_dns": False,
            "hosts_checked": 0,
            "hosts_resolved": 0,
            "hosts_existing": 0,
            "hosts_unavailable": 0,
        }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=_HEADERS)

    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    hosts = candidate_hosts(domain)
    probe_host = f"{_WILDCARD_PROBE_LABEL}.{domain}"

    try:
        results = await asyncio.gather(
            *[_lookup_host(client, host, sem) for host in hosts],
            _lookup_host(client, probe_host, sem),
        )
    finally:
        if owns_client:
            await client.aclose()

    host_results, probe = list(results[:-1]), results[-1]

    # A nonexistent label that still resolves means wildcard DNS.
    wildcard = probe["status"] == STATUS_OK and bool(probe["a"] or probe["cnames"])

    resolved = [r for r in host_results if r["status"] == STATUS_OK]
    unavailable = [r for r in host_results if r["status"] == STATUS_UNAVAILABLE]
    # "resolved" only means the resolver answered — NXDOMAIN is an answer.
    # "existing" is the count that actually has a record, which is the number
    # that matters when reading results.
    existing = [r for r in resolved if r["a"] or r["cnames"]]

    return {
        # Only a total resolver failure counts as unavailable.
        "dns_status": STATUS_UNAVAILABLE if not resolved else STATUS_OK,
        "tools": match_records(host_results, wildcard=wildcard),
        "wildcard_dns": wildcard,
        "hosts_checked": len(host_results),
        "hosts_resolved": len(resolved),
        "hosts_existing": len(existing),
        "hosts_unavailable": len(unavailable),
    }


if __name__ == "__main__":
    import sys

    async def _main(domains: list[str]) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=_HEADERS) as client:
            for domain in domains:
                print(f"\n{'=' * 66}\n{domain}\n{'=' * 66}")
                result = await detect(domain, client)
                if result["dns_status"] == STATUS_VENDOR_EXCLUDED:
                    print("dns_status: vendor_domain_excluded")
                    print("  vendor's own domain — not a prospect, nothing scanned")
                    continue
                print(
                    f"dns_status: {result['dns_status']}   "
                    f"{result['hosts_existing']}/{result['hosts_checked']} hosts exist"
                    f"   ({result['hosts_unavailable']} unresolvable)"
                    f"   wildcard_dns: {result['wildcard_dns']}"
                )
                if result["wildcard_dns"]:
                    print("  note: wildcard DNS — A-record self-hosted signals suppressed")
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
                        f"  {tool['tool']:<30} {tool['confidence']:<5} "
                        f"{tool['signal']:<10} {tool['evidence']}{flag}"
                    )

    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit("usage: python -m app.detectors.dns <domain> [<domain> ...]")
    asyncio.run(_main(argv))

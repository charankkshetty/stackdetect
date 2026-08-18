"""Careers-page keyword scan (FREE) — the recall net.

DNS and CT only catch companies that leak infrastructure into public DNS. Most
real users don't: Airbnb wrote Airflow and runs it heavily, but exposes nothing.
Those companies reveal their stack in public job postings instead, which is why
this detector exists — it trades precision for recall.

It also surfaces SWITCHING TRIGGERS. "You'll maintain our Airflow deployment"
is not just a tool signal, it is a company telling you what it costs them.

CONFIDENCE: tools here score 0.5, below a DNS hit. A job ad mentioning Airflow
is softer proof than a live airflow.company.com. And a job ad NEVER sets the
hard self_hosted_orchestrator flag — DNS means confirmed running, a job ad means
probably running. Those stay distinct.

LEGAL LINE: public careers and job pages only, plain unauthenticated GETs, read
and discard. No login, no auth, no crafted requests. See CLAUDE.md.

Standalone test: python -m app.detectors.careers <domain> [<domain> ...]
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from urllib.parse import urljoin, urlparse

import httpx

from app.patterns import is_vendor_domain

TIMEOUT_SECONDS = 6.0

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_VENDOR_EXCLUDED = "vendor_domain_excluded"

# Bounds on how much we fetch per domain. Reported in the result rather than
# applied silently — a capped scan must not read as a complete one.
MAX_JOB_PAGES_PER_SOURCE = 10
MAX_JOB_PAGES_TOTAL = 20
_MAX_CONCURRENCY = 8

_HEADERS = {"User-Agent": "stackdetect/0.1 (+https://github.com/charankkshetty/stackdetect)"}

# Stack tools. Keyword -> tool name. Matched on word boundaries so "atlan"
# does not fire on "Atlanta" and "dbt" does not fire inside another word.
STACK_KEYWORDS = {
    "airflow": "Apache Airflow",
    "dagster": "Dagster",
    "prefect": "Prefect",
    "kestra": "Kestra",
    "dbt": "dbt",
    "snowflake": "Snowflake",
    "databricks": "Databricks",
    "bigquery": "BigQuery",
    "redshift": "Redshift",
    "fivetran": "Fivetran",
    "airbyte": "Airbyte",
    "looker": "Looker",
    "tableau": "Tableau",
    "great expectations": "Great Expectations",
    "monte carlo": "Monte Carlo",
    "atlan": "Atlan",
}

# Switching triggers — a company describing its own orchestration pain.
TRIGGER_PHRASES = [
    "maintain our airflow",
    "airflow deployment",
    "migrate from airflow",
    "airflow 2",
    "on-call",
    "pipeline reliability",
    "reduce maintenance",
    "self-hosted",
    "orchestration",
]

_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_HREF_RE = re.compile(r"""href=["']([^"'>]+)["']""", re.I)

# Paths that look like an individual posting rather than an index.
_JOB_PATH_RE = re.compile(
    r"/(?:jobs?|positions?|openings?|vacanc(?:y|ies)|j)/[^/?#][^?#]*", re.I
)


def company_slug(domain: str) -> str:
    """monzo.com -> monzo, nubank.com.br -> nubank."""
    d = (domain or "").strip().lower().rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    return d.split(".")[0]


def candidate_sources(domain: str) -> list[str]:
    """Own careers pages first, then the common ATS hosts."""
    slug = company_slug(domain)
    return [
        f"https://{domain}/careers",
        f"https://{domain}/jobs",
        f"https://{domain}/careers/",
        f"https://job-boards.greenhouse.io/{slug}",
        f"https://boards.greenhouse.io/{slug}",
        f"https://jobs.lever.co/{slug}",
        f"https://{slug}.teamtailor.com",
        f"https://apply.workable.com/{slug}",
    ]


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


async def _fetch(
    client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore
) -> tuple[str, str] | None:
    """One public GET. Returns (final_url, markup); None on any failure.

    The FINAL url matters: careers pages redirect constantly
    (acme.com/careers -> careers.acme.com, boards.greenhouse.io ->
    job-boards.greenhouse.io). Resolving links against the pre-redirect url
    makes every on-site job link look off-site and silently yields nothing.
    """
    try:
        async with sem:
            response = await client.get(
                url, follow_redirects=True, timeout=TIMEOUT_SECONDS
            )
        if response.status_code != 200:
            return None
        if "html" not in response.headers.get("content-type", "").lower():
            return None
        return str(response.url), response.text
    except (httpx.HTTPError, UnicodeDecodeError):
        return None


def html_to_text(markup: str) -> str:
    """Strip scripts, tags and entities down to lowercase visible text."""
    text = _SCRIPT_RE.sub(" ", markup or "")
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    return _WS_RE.sub(" ", text).lower()


def job_links(markup: str, source_url: str) -> list[str]:
    """Individual job-posting URLs linked from an index page.

    Same-host links only, so a scan never wanders off the careers site.
    """
    source = urlparse(source_url)
    found: list[str] = []
    seen = set()
    for href in _HREF_RE.findall(markup or ""):
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(source_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or parsed.netloc != source.netloc:
            continue
        if parsed.path.rstrip("/") == source.path.rstrip("/"):
            continue
        if not _JOB_PATH_RE.search(parsed.path):
            continue
        clean = absolute.split("#")[0]
        if clean not in seen:
            seen.add(clean)
            found.append(clean)
    return found[:MAX_JOB_PAGES_PER_SOURCE]


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def _label(url: str) -> str:
    """Short, readable source label for the evidence string."""
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}".rstrip("/") or url


def _word_match(text: str, keyword: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None


def _rank(item: dict) -> tuple:
    return (-item["confidence"], len(item["evidence"]), item["evidence"])


def match_text(pages: list[tuple[str, str]]) -> list[dict]:
    """Scan (source_url, text) pairs for stack tools and switching triggers.

    Deduped so a keyword found across many postings returns once, keeping the
    shortest evidence string. Nothing here sets self_hosted_orchestrator: a job
    ad is not proof the tool is running.
    """
    best: dict[str, dict] = {}

    def add(name: str, keyword: str, source: str, is_trigger: bool) -> None:
        candidate = {
            "tool_or_trigger": name,
            "signal": "job_post",
            "confidence": 0.5,
            "evidence": f'"{keyword}" — {_label(source)}',
            "is_trigger": is_trigger,
            # A job ad is never proof the tool is running. Only a live DNS or
            # CT hit may set these; keeping them hard-False here makes it
            # structurally impossible for hiring text to raise the flag.
            "self_hosted": False,
            "self_hosted_orchestrator": False,
        }
        key = f"{'trigger' if is_trigger else 'tool'}:{name}"
        incumbent = best.get(key)
        if incumbent is None or _rank(candidate) < _rank(incumbent):
            best[key] = candidate

    for source, text in pages:
        if not text:
            continue
        for keyword, tool in STACK_KEYWORDS.items():
            if _word_match(text, keyword):
                add(tool, keyword, source, is_trigger=False)
        for phrase in TRIGGER_PHRASES:
            if phrase in text:
                add(phrase, phrase, source, is_trigger=True)

    return sorted(
        best.values(),
        key=lambda i: (i["is_trigger"], -i["confidence"], i["tool_or_trigger"]),
    )


# --------------------------------------------------------------------------
# Detector entry point
# --------------------------------------------------------------------------


async def detect(domain: str, client: httpx.AsyncClient | None = None) -> dict:
    """Scan `domain`'s public hiring content. Never raises on a fetch failure.

    Returns {"careers_status", "tools", "triggers", "sources_fetched",
    "job_pages_fetched", "job_pages_capped"}.
    """
    domain = domain.strip().lower().rstrip(".")
    if is_vendor_domain(domain):
        return {
            "careers_status": STATUS_VENDOR_EXCLUDED,
            "tools": [],
            "triggers": [],
            "sources_fetched": 0,
            "job_pages_fetched": 0,
            "job_pages_capped": False,
        }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=_HEADERS)

    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    sources = candidate_sources(domain)

    try:
        results = await asyncio.gather(*[_fetch(client, url, sem) for url in sources])

        pages: list[tuple[str, str]] = []
        wanted: list[str] = []
        seen_urls: set[str] = set()
        for result in results:
            if result is None:
                continue
            final_url, markup = result
            # Several candidates commonly redirect to one page; read it once.
            if final_url in seen_urls:
                continue
            seen_urls.add(final_url)
            pages.append((final_url, html_to_text(markup)))
            for link in job_links(markup, final_url):
                if link not in wanted and link not in seen_urls:
                    wanted.append(link)

        capped = len(wanted) > MAX_JOB_PAGES_TOTAL
        wanted = wanted[:MAX_JOB_PAGES_TOTAL]

        job_results = await asyncio.gather(*[_fetch(client, url, sem) for url in wanted])
    finally:
        if owns_client:
            await client.aclose()

    fetched_jobs = 0
    for result in job_results:
        if result is None:
            continue
        final_url, markup = result
        pages.append((final_url, html_to_text(markup)))
        fetched_jobs += 1

    matches = match_text(pages)
    return {
        "careers_status": STATUS_OK if pages else STATUS_UNAVAILABLE,
        "tools": [m for m in matches if not m["is_trigger"]],
        "triggers": [m for m in matches if m["is_trigger"]],
        "sources_fetched": len(seen_urls),
        "job_pages_fetched": fetched_jobs,
        "job_pages_capped": capped,
    }


if __name__ == "__main__":
    import sys

    async def _main(domains: list[str]) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=_HEADERS) as client:
            for domain in domains:
                print(f"\n{'=' * 72}\n{domain}\n{'=' * 72}")
                result = await detect(domain, client)
                if result["careers_status"] == STATUS_VENDOR_EXCLUDED:
                    print("careers_status: vendor_domain_excluded — nothing scanned")
                    continue
                print(
                    f"careers_status: {result['careers_status']}   "
                    f"sources: {result['sources_fetched']}   "
                    f"job pages: {result['job_pages_fetched']}"
                    + ("  (capped)" if result["job_pages_capped"] else "")
                )
                print(f"\n  STACK TOOLS ({len(result['tools'])})")
                for tool in result["tools"] or []:
                    print(f"    {tool['tool_or_trigger']:<22} {tool['confidence']}  {tool['evidence']}")
                if not result["tools"]:
                    print("    (none)")
                print(f"\n  SWITCHING TRIGGERS ({len(result['triggers'])})")
                for trig in result["triggers"] or []:
                    print(f"    {trig['tool_or_trigger']:<22} {trig['confidence']}  {trig['evidence']}")
                if not result["triggers"]:
                    print("    (none)")

    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit("usage: python -m app.detectors.careers <domain> [<domain> ...]")
    asyncio.run(_main(argv))

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
# Greenhouse is where essentially all the real signal is: own-domain careers
# pages are JS shells (26 chars of text) and Lever slug guesses 404. Sample its
# board harder rather than spreading effort across sources that return nothing.
MAX_JOB_PAGES_GREENHOUSE = 20
MAX_JOB_PAGES_TOTAL = 30
GREENHOUSE_HOSTS = ("job-boards.greenhouse.io", "boards.greenhouse.io")
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

# Switching triggers — a company describing its own orchestration pain, in the
# words real job ads actually use. Substring match against lowercased text.
#
# Bare "orchestration" and "on-call" are deliberately NOT here: they are normal
# English and fired on an executive-assistant posting. They only count as part
# of a data-specific phrase below.
TRIGGER_PHRASES = [
    "self-hosted airflow",
    "manage our airflow",
    "own our airflow",
    "airflow at scale",
    "migrating off",
    "migrate from airflow",
    "airflow 2",
    "reduce pipeline",
    "pipeline reliability",
    "on-call for data",
    "data on-call",
    "reduce maintenance burden",
    "own our orchestration",
    "scale our orchestration",
    "data platform reliability",
    "pipeline failures",
    "reduce toil",
]

# A trigger only fires if the SAME posting also talks about data. This is what
# stops a generic phrase in a non-technical ad from counting as buying signal.
DATA_CONTEXT_WORDS = (
    "airflow",
    "dagster",
    "dbt",
    "pipeline",
    "orchestration",
    "data platform",
    "warehouse",
)

# "Monte Carlo" is a statistical method long before it is a data-observability
# vendor, and fintech risk-modelling ads are full of it. Only count it with
# vendor context in the same posting.
MONTE_CARLO_CONTEXT = (
    "monte carlo data",
    "observability",
    "data quality",
    "lineage",
)

_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_HREF_RE = re.compile(r"""href=["']([^"'>]+)["']""", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

# Job titles that mean the company is actively staffing its data platform.
# scoring.py combines this with a self-hosted orchestrator to reach HOT: they
# are running the thing AND paying people to keep it running.
DATA_ROLE_KEYWORDS = (
    "data engineer",
    "platform engineer",
    "analytics engineer",
    "data infrastructure",
    "data platform",
)

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


def page_limit(url: str) -> int:
    """How many postings to sample from one source."""
    host = urlparse(url).netloc.lower()
    return MAX_JOB_PAGES_GREENHOUSE if host in GREENHOUSE_HOSTS else MAX_JOB_PAGES_PER_SOURCE


def job_links(markup: str, source_url: str, limit: int | None = None) -> list[str]:
    """Individual job-posting URLs linked from an index page.

    Same-host links only, so a scan never wanders off the careers site.
    """
    if limit is None:
        limit = page_limit(source_url)
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
    return found[:limit]


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def page_title(markup: str) -> str:
    """The <title> of a job page, cleaned. Empty string if absent."""
    match = _TITLE_RE.search(markup or "")
    if not match:
        return ""
    return _WS_RE.sub(" ", html_lib.unescape(_TAG_RE.sub(" ", match.group(1)))).strip()


def is_data_role(title: str) -> bool:
    """True if this job title is a data- or platform-engineering role."""
    lowered = (title or "").lower()
    return any(keyword in lowered for keyword in DATA_ROLE_KEYWORDS)


def _label(url: str) -> str:
    """Short, readable source label for the evidence string."""
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}".rstrip("/") or url


def _word_match(text: str, keyword: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None


def _rank(item: dict) -> tuple:
    return (-item["confidence"], len(item["evidence"]), item["evidence"])


def has_data_context(text: str) -> bool:
    """True if this posting talks about data at all."""
    return any(word in text for word in DATA_CONTEXT_WORDS)


def monte_carlo_is_vendor(text: str) -> bool:
    """Distinguish the vendor from the statistical method."""
    return any(word in text for word in MONTE_CARLO_CONTEXT)


def match_text(pages: list[tuple[str, str]]) -> list[dict]:
    """Scan (source_url, text) pairs for stack tools and switching triggers.

    Context is evaluated PER PAGE, not across the whole scan: a trigger phrase
    counts only when that same posting also talks about data, and "monte carlo"
    counts only with data-observability context in that posting.

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
            if not _word_match(text, keyword):
                continue
            # Drop the statistical method; keep the vendor.
            if keyword == "monte carlo" and not monte_carlo_is_vendor(text):
                continue
            add(tool, keyword, source, is_trigger=False)

        # Triggers require data context in the same posting.
        if not has_data_context(text):
            continue
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
            "hiring_data_roles": False,
            "data_role_count": 0,
            "data_roles": [],
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
            for link in job_links(markup, final_url, page_limit(final_url)):
                if link not in wanted and link not in seen_urls:
                    wanted.append(link)

        capped = len(wanted) > MAX_JOB_PAGES_TOTAL
        wanted = wanted[:MAX_JOB_PAGES_TOTAL]

        job_results = await asyncio.gather(*[_fetch(client, url, sem) for url in wanted])
    finally:
        if owns_client:
            await client.aclose()

    fetched_jobs = 0
    data_roles: list[dict] = []
    for result in job_results:
        if result is None:
            continue
        final_url, markup = result
        pages.append((final_url, html_to_text(markup)))
        fetched_jobs += 1
        title = page_title(markup)
        if is_data_role(title):
            data_roles.append({"title": title, "url": final_url})

    matches = match_text(pages)
    return {
        "careers_status": STATUS_OK if pages else STATUS_UNAVAILABLE,
        "tools": [m for m in matches if not m["is_trigger"]],
        "triggers": [m for m in matches if m["is_trigger"]],
        "sources_fetched": len(seen_urls),
        "job_pages_fetched": fetched_jobs,
        "job_pages_capped": capped,
        # Actively staffing the data platform — half of the HOT co-occurrence.
        "hiring_data_roles": bool(data_roles),
        "data_role_count": len(data_roles),
        "data_roles": data_roles,
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
                print(
                    f"data/platform roles hiring: {result['data_role_count']}"
                )
                for role in result["data_roles"][:5]:
                    print(f"    {role['title'][:74]}")
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

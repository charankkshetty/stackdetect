"""Computes stack / trigger / fit scores and builds the evidence list.

Pure functions over data — no network calls belong here. Takes the result dicts
from ct_logs, dns and careers for ONE domain and produces the verdict a rep
acts on.

THREE SCORES
  stack    what they run. Distinct tools, each with its best evidence and the
           signals that found it. A tool confirmed by two independent signals
           outranks one seen only once.
  trigger  the commercial switching signal, computed as CO-OCCURRENCE across
           detectors rather than keywords — job ads list technologies, they do
           not describe pain, so the signal is structural:
             HOT  a self-hosted orchestrator confirmed live in DNS/CT. They are
                  running the exact thing Orchestra replaces.
             HOT  a self-hosted orchestrator named in job ads AND active
                  data/platform hiring. Running it and staffing the pain.
             WARM a self-hosted orchestrator from one softer signal only.
             COLD managed stack only, or no orchestrator at all.
  fit      not computed yet — Apollo fills this in the next step.

Every trigger carries the hostnames or job postings that drove it, so a rep can
defend the claim on a call.

Self-test: python -m app.scoring
"""

from __future__ import annotations

HOT = "HOT"
WARM = "WARM"
COLD = "COLD"

EXCLUDED = "EXCLUDED"

_LEVEL_ORDER = {HOT: 0, WARM: 1, COLD: 2, EXCLUDED: 3}

# Why a trigger fired. Two HOTs are not the same prospect:
#   confirmed_running  a live DNS/CT hit — the host resolves, stable scan to
#                      scan, and the strongest thing a rep can quote.
#   likely_running     job-ad co-occurrence — real signal, but job boards
#                      rotate, so it can move between scans.
BASIS_CONFIRMED = "confirmed_running"
BASIS_LIKELY = "likely_running"
BASIS_NAMED = "named_only"


def trigger_basis(level: str, self_hosted_orchestrator: bool) -> str | None:
    """Classify WHY the trigger fired, for the UI to surface.

    Exact by construction: a live signal always sets the hard flag and a
    co-occurrence HOT never does.
    """
    if level == HOT:
        return BASIS_CONFIRMED if self_hosted_orchestrator else BASIS_LIKELY
    if level == WARM:
        return BASIS_NAMED
    return None

# Tools that ARE orchestration — the products Orchestra displaces.
ORCHESTRATOR_TOOLS = frozenset({
    "Apache Airflow",
    "Dagster",
    "Prefect",
    "Kestra",
})

# Signals that prove a tool is actually running, as opposed to merely mentioned.
LIVE_SIGNALS = frozenset({"ct_log", "dns_a", "dns_cname"})

_SELF_HOSTED_SUFFIX = " (self-hosted)"


def canonical_tool(name: str) -> str:
    """Merge naming across detectors.

    ct_logs/dns emit "Apache Airflow (self-hosted)" while careers emits
    "Apache Airflow"; they are the same tool and must combine for
    cross-signal confirmation. Deployment mode is not lost — it stays on each
    entry's self_hosted flag.
    """
    name = (name or "").strip()
    if name.endswith(_SELF_HOSTED_SUFFIX):
        return name[: -len(_SELF_HOSTED_SUFFIX)]
    return name


def _normalise(entry: dict) -> dict:
    """One evidence dict from any detector, in one shape."""
    raw_name = entry.get("tool") or entry.get("tool_or_trigger") or ""
    return {
        "name": canonical_tool(raw_name),
        "raw_name": raw_name,
        "signal": entry.get("signal", ""),
        "confidence": float(entry.get("confidence") or 0.0),
        "evidence": entry.get("evidence", ""),
        "self_hosted": bool(entry.get("self_hosted")),
        "self_hosted_orchestrator": bool(entry.get("self_hosted_orchestrator")),
    }


def collect_evidence(ct: dict | None, dns: dict | None, careers: dict | None) -> list[dict]:
    """Flatten every detector's tool evidence into one normalised list."""
    entries = []
    for result in (ct, dns, careers):
        for entry in (result or {}).get("tools") or []:
            entries.append(_normalise(entry))
    return entries


# --------------------------------------------------------------------------
# stack
# --------------------------------------------------------------------------


def score_stack(entries: list[dict]) -> list[dict]:
    """Distinct tools, best evidence each, ranked by corroboration.

    Two independent signals naming the same tool is materially stronger than
    one, so confirmations sort first.
    """
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        if entry["name"]:
            grouped.setdefault(entry["name"], []).append(entry)

    tools = []
    for name, group in grouped.items():
        best = max(group, key=lambda e: (e["confidence"], -len(e["evidence"])))
        signals = sorted({e["signal"] for e in group if e["signal"]})
        tools.append({
            "tool": name,
            "confidence": best["confidence"],
            "evidence": best["evidence"],
            "signals": signals,
            "confirmations": len(signals),
            "self_hosted": any(e["self_hosted"] for e in group),
            # Only a live signal may assert this; careers never sets it.
            "self_hosted_orchestrator": any(e["self_hosted_orchestrator"] for e in group),
            "all_evidence": [
                {"signal": e["signal"], "evidence": e["evidence"], "confidence": e["confidence"]}
                for e in sorted(group, key=lambda x: -x["confidence"])
            ],
        })

    return sorted(
        tools,
        key=lambda t: (-t["confirmations"], -t["confidence"], t["tool"]),
    )


# --------------------------------------------------------------------------
# trigger
# --------------------------------------------------------------------------


def score_trigger(entries: list[dict], careers: dict | None) -> tuple[str, list[dict]]:
    """Trigger level plus the evidence that justifies it."""
    careers = careers or {}
    hiring = bool(careers.get("hiring_data_roles"))
    data_roles = careers.get("data_roles") or []

    # Confirmed running: only a live DNS/CT signal sets self_hosted_orchestrator.
    live = [
        e for e in entries
        if e["self_hosted_orchestrator"] and e["signal"] in LIVE_SIGNALS
    ]
    # Named in hiring content: softer — they say they use it.
    mentioned = [
        e for e in entries
        if e["name"] in ORCHESTRATOR_TOOLS and e["signal"] not in LIVE_SIGNALS
    ]

    if live:
        evidence = [{
            "reason": "self-hosted orchestrator confirmed live",
            "detail": f"{e['name']} — {e['evidence']}",
            "signal": e["signal"],
        } for e in sorted(live, key=lambda e: -e["confidence"])]
        return HOT, evidence

    if mentioned and hiring:
        evidence = [{
            "reason": "self-hosted orchestrator named in job ads",
            "detail": f"{e['name']} — {e['evidence']}",
            "signal": e["signal"],
        } for e in sorted(mentioned, key=lambda e: -e["confidence"])]
        evidence += [{
            "reason": "actively hiring data/platform engineers",
            "detail": f"{role.get('title', '')} — {role.get('url', '')}",
            "signal": "job_post",
        } for role in data_roles[:5]]
        return HOT, evidence

    if mentioned:
        evidence = [{
            "reason": "self-hosted orchestrator named, but no data hiring seen",
            "detail": f"{e['name']} — {e['evidence']}",
            "signal": e["signal"],
        } for e in sorted(mentioned, key=lambda e: -e["confidence"])]
        return WARM, evidence

    return COLD, []


# --------------------------------------------------------------------------
# verdict
# --------------------------------------------------------------------------


def build_verdict(
    domain: str,
    ct: dict | None = None,
    dns: dict | None = None,
    careers: dict | None = None,
) -> dict:
    """The final object for one domain."""
    entries = collect_evidence(ct, dns, careers)
    tools = score_stack(entries)
    trigger_level, trigger_evidence = score_trigger(entries, careers)
    hard_flag = any(t["self_hosted_orchestrator"] for t in tools)

    return {
        "domain": domain,
        "tools": tools,
        "trigger_level": trigger_level,
        "trigger_evidence": trigger_evidence,
        # Hard flag: live DNS/CT only. A job ad is never proof it is running.
        "self_hosted_orchestrator": hard_flag,
        "trigger_basis": trigger_basis(trigger_level, hard_flag),
        # What each detector could actually see — a COLD verdict from a failed
        # scan is not the same as a COLD verdict from a complete one.
        "signals": {
            "ct_logs": (ct or {}).get("ct_status", "not_run"),
            "dns": (dns or {}).get("dns_status", "not_run"),
            "careers": (careers or {}).get("careers_status", "not_run"),
            "data_role_count": (careers or {}).get("data_role_count", 0),
        },
        # Placeholder — Apollo enrichment fills this in the next step.
        "fit": None,
    }


def sort_verdicts(verdicts: list[dict]) -> list[dict]:
    """HOT first, then by corroborated stack depth. Rep works top-down.

    Within a level, a confirmed-running hit outranks a likely-running one: a
    resolving host is stable and quotable, while job-ad co-occurrence can move
    between scans as boards rotate.
    """
    return sorted(
        verdicts,
        key=lambda v: (
            _LEVEL_ORDER.get(v["trigger_level"], 9),
            0 if v.get("self_hosted_orchestrator") else 1,
            -sum(t.get("confirmations", len(t.get("signals") or [])) for t in v["tools"]),
            -len(v["tools"]),
            v["domain"],
        ),
    )


if __name__ == "__main__":
    import json
    import sys

    def render(verdict: dict) -> None:
        print(f"\n{'=' * 74}")
        print(f"{verdict['domain']}    TRIGGER: {verdict['trigger_level']}"
              f"    self_hosted_orchestrator={verdict['self_hosted_orchestrator']}"
              f"    fit={verdict['fit']}")
        print(f"{'=' * 74}")
        print(f"  signals: {verdict['signals']}")
        print(f"\n  STACK ({len(verdict['tools'])})")
        for tool in verdict["tools"] or []:
            mark = " *" if tool["confirmations"] > 1 else "  "
            print(f"  {mark}{tool['tool']:<24} {tool['confidence']:<5} "
                  f"{'+'.join(tool['signals']):<22} {tool['evidence'][:52]}")
        if not verdict["tools"]:
            print("    (none)")
        print(f"\n  TRIGGER EVIDENCE ({len(verdict['trigger_evidence'])})")
        for item in verdict["trigger_evidence"] or []:
            print(f"    [{item['signal']}] {item['reason']}")
            print(f"        {item['detail'][:86]}")
        if not verdict["trigger_evidence"]:
            print("    (none — no self-hosted orchestrator detected)")

    argv = sys.argv[1:]
    if not argv:
        raise SystemExit(
            "usage: python -m app.scoring <scan-fixture.json>\n"
            "  fixture: {domain: {ct:..., dns:..., careers:...}, ...}"
        )
    with open(argv[0], encoding="utf-8") as fh:
        fixtures = json.load(fh)

    verdicts = [
        build_verdict(domain, r.get("ct"), r.get("dns"), r.get("careers"))
        for domain, r in fixtures.items()
    ]
    for verdict in sort_verdicts(verdicts):
        render(verdict)
    print(f"\n{'-' * 74}\nsorted HOT-first: "
          f"{[ (v['domain'], v['trigger_level']) for v in sort_verdicts(verdicts) ]}")

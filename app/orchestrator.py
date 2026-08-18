"""Runs every detector for ONE domain and assembles the verdict.

Future role: fan out the four FREE detectors (ct_logs, dns, fingerprint,
careers) concurrently over a shared httpx.AsyncClient, collect their raw
signals, hand them to scoring.py, and only then — if the free stack score is
greater than zero — call the GATED paid enrichers (apollo, parallel) through
app.ledger. Returns the verdict dict that POST /scan serves: domain, tools[],
self_hosted_orchestrator, credits.
"""

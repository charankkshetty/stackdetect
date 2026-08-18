"""Parallel page reading — GATED + CACHED + CAPPED (PAID).

Future role: read and summarise public pages (engineering blog, careers, docs)
for a company that has ALREADY scored stack>0, to enrich the evidence string
with a human-readable reason.

Must be called only through app.ledger, which owns the gate, the per-domain
disk cache, and the 300-credit hard cap. Key from env PARALLEL_API_KEY.
"""

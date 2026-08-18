"""Apollo org enrichment — GATED + CACHED + CAPPED (PAID).

Future role: add firmographics (headcount, industry, funding) to a company that
has ALREADY scored stack>0 from the free detectors, so we never spend a credit
on a domain with no detected stack.

Must be called only through app.ledger, which owns the gate, the per-domain
disk cache, and the 300-credit hard cap. Key from env APOLLO_API_KEY.
"""

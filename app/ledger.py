"""Credit safety for paid APIs: gate + on-disk cache + hard stop.

Future role: the SINGLE place any paid provider (Apollo, Parallel) may be
called from, so credit safety cannot leak into individual detectors.

Responsibilities:
- GATE: refuse to enrich a domain whose free-detector stack score is 0.
- CACHE: persist every paid result to cache/<provider>/<domain>.json, so
  re-scanning a domain costs 0 credits.
- CAP: track spend per provider in a persisted ledger (cache/ledger.json) with
  a HARD CAP of 300 credits each. At the cap, return "cap reached" — never call
  the API, never raise.
"""

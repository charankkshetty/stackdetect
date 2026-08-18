# stackdetect — project memory

## What this is
A GTM targeting tool for Orchestra (data orchestration vendor). Input: one
domain or many (oine). Output: a table showing which data-stack tools
each company uses, WHY we believe it (an evidence string per tool), and a flag
for companies running self-hosted Airflow — Orchestra's #1 buying signal.

## The differentiator — keep it front and centre
We detect the data stack by RESOLVING PUBLIC INFRASTRUCTURE — DNS records and
certificate-transparency logs — not by buying a third-party technographic list.
BuiltWith / ZoomInfo / Wappalyzer read website page-source and are BLIND to the
data layer (Snowflake, BigQuery, dbt, Looker leave no website trace). We go a
layer deeper. The UI must visibly state the method: "DNS + Certificate
Transparency". Tagline: "We don't buy technographics. We resolve them."

## Hard rules
- FREE detectors run on every domain: crt.sh (CT logs), DNS-over-HTTPS,
  HTTP title-read fingerprint, careers-page keyword scan.
- PAID enrichers (Apollo, Parallel) are GATED: only call on a company that
  already scored stack>0 from the free detectors. Never enrich a domain with noted stack.
- Every paid result is CACHED to disk by domain. Re-scanning a domain = 0 credits.
- Each paid provider has a HARD CAP of 300 credits, tracked in a persisted
  ledger. At the cap: return "cap reached", never call the API, never crash.
- LEGAL LINE (non-negotiable): we only READ public data — DNS, CT logs, public
  careers pages. The HTTP fingerprint makes ONE unauthenticated GET and reads the
  page title only. Never a login attempt, never a second crafted request, never
  anything requiring auth. Do not add any detector that touches a private or
  authenticated system.

## Stack
Python 3.11, FastAPI, httpx (async), uvicorn. Deploy on Railway via Procfile.
No database — JSON cache on disk under cache/. Secrets from env vars
APOLLO_API_KEY and PARALLEL_API_KEY. Never commit secrets.

## Build principle
Deploy-first: get an empty app live on Railway before adding detectors. Build
detectors one at a time, each independently testable. Always keep the app
shippable. Detectors live in app/detectors independent modules. All credit
safety (gate, cap, cache) lives in app/ledger.py so it can't leak.

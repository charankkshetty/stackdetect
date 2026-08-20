# stackdetect

Detect a company's data stack from public infrastructure signals, no technographic database required.

`stackdetect` takes a domain and resolves what data tooling a company runs by reading signals that are already public: DNS records, certificate transparency logs, and careers pages. It was built to answer a specific question for [Orchestra](https://getorchestra.io): **which companies are running a self-hosted orchestrator right now**, because that is the strongest signal that a team has outgrown its current setup and is a live buyer.

The write-up for the Orchestra targeting task is in [ASSIGNMENT.md](./ASSIGNMENT.md).

## Why this exists

Technographic databases like BuiltWith and ZoomInfo read a company's website source code. That works for marketing tools, but the data stack leaves no trace on the marketing site: Snowflake, Airflow, and dbt do not show up in a homepage's HTML. So those databases are effectively blind to the data layer.

`stackdetect` goes to the infrastructure layer instead, where the data stack actually lives. Rather than buy technographics, it resolves them.

## How it works

For each domain, the tool runs three independent checks and combines the signals.

### 1. DNS over HTTPS

The tool queries public DNS resolvers (Google's `dns.google/resolve` as primary, Cloudflare `1.1.1.1` as fallback) for a fixed list of candidate subdomains: `airflow.`, `dagster.`, `metabase.`, `looker.`, `snowflake.`, and others. It reads two record types:

- **CNAME**: if `looker.company.com` points at `looker.com`, that is a managed vendor tool.
- **A record**: if `airflow.company.com` resolves to a live IP, that is a self-hosted deployment. A private IP range (for example `10.x`) leaking into public DNS is the strongest displacement signal of all, an internal orchestrator exposed to the world.

### 2. Certificate transparency

The tool queries certificate transparency logs (via crt.sh), the public append-only record of every TLS certificate issued, to find subdomains that exist but may not resolve in DNS. This widens coverage beyond the DNS probe.

### 3. Careers scan

The tool reads the company's public job postings for stack mentions (Snowflake, dbt, Airflow) and hiring signals, which corroborate the infrastructure findings and add a maturity signal.

## Signals it returns

Each domain is classified by trigger strength:

- **HOT**: a live self-hosted orchestrator, or a clear co-occurrence of an orchestrator plus active data hiring. A buyer now.
- **WARM**: an orchestrator named but no current corroborating signal.
- **COLD**: modern stack detected (warehouse, BI, dbt) but no orchestration displacement signal, or nothing exposed.

Everything is derived from public infrastructure. No scanning, no access, no credentials, just resolution.

## Running it

The tool is deployed and can be run against a list of domains through the web interface. Paste one domain per line and it returns the trigger classification, detected tools, and the evidence for each.

## Where it fits

`stackdetect` is one layer of a larger targeting system described in [ASSIGNMENT.md](./ASSIGNMENT.md): Apollo builds and enriches the qualified account list (firmographics, data-team size, warehouse, dbt), and `stackdetect` confirms the live displacement signal on the hottest accounts, the self-hosted orchestrator that bought technographic data misses.

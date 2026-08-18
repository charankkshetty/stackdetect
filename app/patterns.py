"""Vendor DNS / CT fingerprint patterns — pure data, no logic.

The lookup table mapping observable public infrastructure to a vendor. Adding
a vendor is a data edit here; the matching logic lives in the detectors.

Each entry is {pattern, tool, confidence, kind, orchestrator}:

  kind          "saas"        substring match anywhere in the hostname
                "self_hosted" prefix match on "<pattern>." — the label must be
                              a subdomain of the target (airflow.acme.com)
  orchestrator  True only for real orchestrators. Orchestra's #1 buying signal
                is self-hosted ORCHESTRATION; a Metabase or Superset hit is
                useful stack signal but must never raise that flag.

WHICH PATTERNS ARE MATCHABLE WHERE
- SELF_HOSTED_PATTERNS are TARGET-DOMAIN-MATCHABLE: a crt.sh query for
  %.{domain} returns hostnames under the target's own domain, which is exactly
  where airflow.acme.com lives. ct_logs.py applies these.
- MANAGED_SAAS_PATTERNS are NOT target-domain-matchable: a company's Snowflake
  account is acme.snowflakecomputing.com, which sits under the VENDOR's domain
  and can never appear in a %.{acme.com} query. These are reached either by
  CNAME lookup (dns.py — bi.acme.com -> looker) or by a future inverted CT
  vendor-scan module that queries %.snowflakecomputing.com and matches account
  names back to companies. Kept here as data; not applied by ct_logs.py.

Confidences are deliberately conservative — evidence a rep has to defend on a
call is worth more than a high score.
"""

# TARGET-DOMAIN-MATCHABLE. The company runs the tool itself on its own domain.
SELF_HOSTED_PATTERNS = [
    {"pattern": "airflow", "tool": "Apache Airflow (self-hosted)", "confidence": 0.6, "kind": "self_hosted", "orchestrator": True},
    {"pattern": "dagster", "tool": "Dagster (self-hosted)", "confidence": 0.6, "kind": "self_hosted", "orchestrator": True},
    {"pattern": "prefect", "tool": "Prefect (self-hosted)", "confidence": 0.6, "kind": "self_hosted", "orchestrator": True},
    {"pattern": "kestra", "tool": "Kestra (self-hosted)", "confidence": 0.6, "kind": "self_hosted", "orchestrator": True},
    # Self-hosted BI, NOT orchestrators — real stack signal, but they must not
    # raise the self-hosted-orchestrator buying signal.
    {"pattern": "metabase", "tool": "Metabase (self-hosted)", "confidence": 0.6, "kind": "self_hosted", "orchestrator": False},
    {"pattern": "superset", "tool": "Superset (self-hosted)", "confidence": 0.6, "kind": "self_hosted", "orchestrator": False},
]

# NOT TARGET-DOMAIN-MATCHABLE — needs CNAME lookup (dns.py) or an inverted CT
# vendor scan. Retained as data; deliberately not in CT_PATTERNS.
MANAGED_SAAS_PATTERNS = [
    {"pattern": "snowflakecomputing.com", "tool": "Snowflake", "confidence": 0.95, "kind": "saas", "orchestrator": False},
    {"pattern": "dagster.cloud", "tool": "Dagster+", "confidence": 0.95, "kind": "saas", "orchestrator": True},
    {"pattern": "dagster.plus", "tool": "Dagster+", "confidence": 0.95, "kind": "saas", "orchestrator": True},
    {"pattern": "astronomer.run", "tool": "Astronomer", "confidence": 0.95, "kind": "saas", "orchestrator": True},
    {"pattern": ".dbt.com", "tool": "dbt Cloud", "confidence": 0.9, "kind": "saas", "orchestrator": False},
    {"pattern": "getdbt.com", "tool": "dbt Cloud", "confidence": 0.9, "kind": "saas", "orchestrator": False},
    {"pattern": "coalescesoftware.io", "tool": "Coalesce", "confidence": 0.95, "kind": "saas", "orchestrator": False},
    {"pattern": "azuresynapse.net", "tool": "Azure Synapse", "confidence": 0.9, "kind": "saas", "orchestrator": False},
    {"pattern": ".collibra.com", "tool": "Collibra", "confidence": 0.9, "kind": "saas", "orchestrator": False},
    {"pattern": ".looker.com", "tool": "Looker", "confidence": 0.9, "kind": "saas", "orchestrator": False},
    {"pattern": "looker.cloud.com", "tool": "Looker", "confidence": 0.9, "kind": "saas", "orchestrator": False},
    {"pattern": "cloud.databricks.com", "tool": "Databricks", "confidence": 0.9, "kind": "saas", "orchestrator": False},
    {"pattern": "azuredatabricks.net", "tool": "Databricks", "confidence": 0.9, "kind": "saas", "orchestrator": False},
    {"pattern": "gcp.databricks.com", "tool": "Databricks", "confidence": 0.9, "kind": "saas", "orchestrator": False},
]

# What ct_logs.py applies: self-hosted only. The managed patterns are excluded
# because a %.{domain} query can never surface them (see module docstring).
CT_PATTERNS = SELF_HOSTED_PATTERNS


# --------------------------------------------------------------------------
# Vendor / project domains
# --------------------------------------------------------------------------
#
# The data-tool vendors' and projects' OWN domains. A vendor running its own
# software is not a sales prospect, and reporting "apache.org runs Apache
# Airflow" inflates any accuracy measurement with a claim nobody would act on.
#
# THIS IS NOT A FILTER ON OPEN SOURCE. A normal company running self-hosted
# Airflow, Superset or Metabase is exactly the target and must stay in —
# nubank.com.br running self-hosted Airflow is the signal we are built to find.
# Only these specific vendor-owned domains are excluded.
#
# Pure data: extend by adding a domain.
VENDOR_DOMAINS = frozenset({
    "apache.org",
    "superset.apache.org",
    "dagster.io",
    "dagster.cloud",
    "prefect.io",
    "getdbt.com",
    "dbt.com",
    "astronomer.io",
    "snowflake.com",
    "databricks.com",
    "coalescesoftware.io",
    "kestra.io",
    "metabase.com",
    "looker.com",
    "collibra.com",
    "montecarlodata.com",
    "getmontecarlo.com",
})


def is_vendor_domain(domain: str) -> bool:
    """True if `domain` is a vendor's own domain, or a subdomain of one.

    The one predicate that lives beside this data, so both ct_logs.py and
    dns.py share a single implementation of the membership rule rather than
    each carrying its own copy. Subdomains count, so www.apache.org and
    airflow.apache.org are both excluded, while notapache.org is not.
    """
    d = (domain or "").strip().lower().rstrip(".")
    return any(d == vendor or d.endswith(f".{vendor}") for vendor in VENDOR_DOMAINS)

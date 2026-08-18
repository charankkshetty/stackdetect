"""crt.sh certificate-transparency lookup (FREE).

Future role: query crt.sh for every certificate ever issued to *.<domain> and
return the subdomains found. This is the core differentiator — CT logs expose
internal hostnames (snowflake, looker, airflow, dbt) that never appear in
website page-source, which is why BuiltWith and ZoomInfo are blind to the data
layer. Public read only.
"""

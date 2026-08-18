"""Vendor DNS / CT fingerprint patterns — pure data, no logic.

Future role: the lookup table mapping observable public infrastructure to a
vendor, e.g. a *.snowflakecomputing.com CT entry implies Snowflake, a CNAME to
a Looker or Fivetran host implies those, an airflow.* / *.astronomer.* host
implies an orchestrator. Each entry carries the tool name, the signal type, and
a confidence, so scoring.py stays generic and adding a vendor is a data edit.
"""

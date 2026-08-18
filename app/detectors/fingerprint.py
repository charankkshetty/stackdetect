"""HTTP title-read fingerprint (FREE).

Future role: make exactly ONE unauthenticated GET to a resolved host and read
the page <title> only, to confirm what is running there (e.g. an Airflow login
page titled "Sign In - Airflow" confirms self-hosted Airflow).

LEGAL LINE: one unauthenticated GET, title only. Never a login attempt, never a
second crafted request, never anything requiring auth.
"""

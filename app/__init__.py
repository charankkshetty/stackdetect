"""stackdetect application package.

Holds the scan pipeline: orchestrator (runs detectors for one domain),
scoring (turns raw signals into stack/trigger/fit scores + evidence),
ledger (all paid-API credit safety), patterns (vendor fingerprint data),
and the detectors package itself.
"""

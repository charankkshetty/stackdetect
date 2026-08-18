"""Computes stack / trigger / fit scores and builds the evidence list.

Future role: take the raw signals emitted by the detectors and turn them into
(a) a per-tool record {tool, signal, confidence, evidence} where `evidence` is
the literal public string we resolved (a CT-log hostname, a CNAME target) so a
rep can defend the claim on a call, (b) a stack score used as the paid-enricher
gate, and (c) the self_hosted_orchestrator flag — Orchestra's #1 buying signal.
Pure functions over data: no network calls belong here.
"""

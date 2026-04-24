"""
Phase 7 — Hiring-manager & recruiter discovery.

Public entrypoints:
    - ApolloClient        — REST wrapper around Apollo.io people-search API.
    - EnrichmentPipeline  — orchestrates eligibility, cache, guardrails, upsert.
    - ContactGuardrails   — per-company + daily-cap budget enforcement.
"""

from backend.contacts.apollo_client import ApolloClient, ApolloContact
from backend.contacts.cost_guardrails import ContactGuardrails, GuardrailDecision
from backend.contacts.enrichment_pipeline import EnrichmentPipeline, EnrichmentResult

__all__ = [
    "ApolloClient",
    "ApolloContact",
    "ContactGuardrails",
    "GuardrailDecision",
    "EnrichmentPipeline",
    "EnrichmentResult",
]

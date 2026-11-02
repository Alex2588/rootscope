from enum import StrEnum


class InvestigationStrategy(StrEnum):
    LLM_ONLY = "llm_only"
    TELEMETRY = "telemetry"
    TELEMETRY_RAG = "telemetry_rag"
    AGENTIC = "agentic"


class InvestigationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

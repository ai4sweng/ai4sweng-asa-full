"""
Central constants for AI4SWEng KIO1 MVP.

All shared architectural constants live here. Environment-specific settings
belong in config.py — never mix the two.
"""

from enum import StrEnum

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

# Protocol version stamped on every message envelope for forward compatibility.
# Used by: messaging/envelope.py, persistence message storage.
PROTOCOL_VERSION: str = "1.0.0"

# ---------------------------------------------------------------------------
# NATS Subjects
# ---------------------------------------------------------------------------


class NatsSubject(StrEnum):
    """
    NATS subject names for workflow routing and agent pub/sub.

    Completion subjects double as dispatch triggers — orchestrator publishes
    TASK_REQUEST on the same subject it listens to for TASK_RESPONSE.
    """

    WORKFLOW_START = "workflow.start"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"
    PLANNER_COMPLETED = "planner.completed"
    CODE_GENERATED = "code.generated"
    DEBUG_COMPLETED = "debug.completed"
    FIX_COMPLETED = "fix.completed"
    HUMAN_APPROVAL_REQUESTED = "human.approval.requested"
    HUMAN_APPROVAL_GRANTED = "human.approval.granted"
    HUMAN_APPROVAL_REJECTED = "human.approval.rejected"
    AGENT_CAPABILITY_ANNOUNCEMENT = "agent.capability.announcement"
    AGENT_HEARTBEAT = "agent.heartbeat"
    TASK_STATUS = "task.status"
    TASK_FAILED = "task.failed"
    ARTIFACT_CREATED = "artifact.created"

    # Repo audit workflow — targets external buggy FastAPI repository.
    REPO_WORKFLOW_START = "repo.workflow.start"
    REPO_ANALYSIS_COMPLETED = "repo.analysis.completed"
    REPO_TESTS_GENERATED = "repo.tests.generated"
    REPO_BUGS_DETECTED = "repo.bugs.detected"
    REPO_PATCH_GENERATED = "repo.patch.generated"
    REPO_TESTS_RERUN = "repo.tests.rerun"
    REPO_EVIDENCE_REPORTED = "repo.evidence.reported"


# ---------------------------------------------------------------------------
# Message Types
# ---------------------------------------------------------------------------


class MessageType(StrEnum):
    """Unified envelope message_type values per AI4SWEng interface model."""

    CAPABILITY_ANNOUNCEMENT = "CAPABILITY_ANNOUNCEMENT"
    HEARTBEAT = "HEARTBEAT"
    TASK_REQUEST = "TASK_REQUEST"
    TASK_ACK = "TASK_ACK"
    TASK_STATUS = "TASK_STATUS"
    TASK_RESPONSE = "TASK_RESPONSE"
    TASK_FAILED = "TASK_FAILED"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    HUMAN_APPROVAL_REQUEST = "HUMAN_APPROVAL_REQUEST"
    HUMAN_APPROVAL_RESPONSE = "HUMAN_APPROVAL_RESPONSE"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"


# ---------------------------------------------------------------------------
# Workflow States
# ---------------------------------------------------------------------------


class WorkflowState(StrEnum):
    """Workflow state machine states — orchestrator WorkflowManager."""

    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_HUMAN_APPROVAL = "WAITING_FOR_HUMAN_APPROVAL"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Task States
# ---------------------------------------------------------------------------


class TaskState(StrEnum):
    """Task state machine states — orchestrator TaskScheduler."""

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RUNNING = "RUNNING"
    PROGRESSING = "PROGRESSING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"


# ---------------------------------------------------------------------------
# Orchestrator States
# ---------------------------------------------------------------------------


class OrchestratorState(StrEnum):
    """Orchestrator internal state machine."""

    INITIALIZING = "INITIALIZING"
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    RECOVERY = "RECOVERY"
    SHUTDOWN = "SHUTDOWN"


# ---------------------------------------------------------------------------
# Agent Roles
# ---------------------------------------------------------------------------


class AgentRole(StrEnum):
    """Registered agent roles for capability matching and dispatch."""

    PLANNER = "PlannerAgent"
    CODE_GENERATOR = "CodeGeneratorAgent"
    DEBUGGER = "DebuggerAgent"
    FIXER_REPORTER = "FixerReporterAgent"
    ORCHESTRATOR = "Orchestrator"
    # Repo audit pipeline agents (buggy-repo demonstrations).
    REPO_ANALYZER = "RepoAnalyzerAgent"
    TEST_GENERATOR = "TestGeneratorAgent"
    BUG_DETECTOR = "BugDetectorAgent"
    PATCH_AGENT = "PatchAgent"
    TEST_RERUN = "TestRerunAgent"
    EVIDENCE_REPORT = "EvidenceReportAgent"


# ---------------------------------------------------------------------------
# Artifact Types
# ---------------------------------------------------------------------------


class ArtifactType(StrEnum):
    """Artifact types forming the provenance lineage chain."""

    PLAN = "PLAN"
    SOURCE_CODE = "SOURCE_CODE"
    DEBUG_REPORT = "DEBUG_REPORT"
    FIXED_CODE = "FIXED_CODE"
    FINAL_REPORT = "FINAL_REPORT"
    # Repo audit artifact lineage (separate from Fibonacci demo).
    ISSUE = "ISSUE"
    REPO_ANALYSIS = "REPO_ANALYSIS"
    # Supplementary repo-analysis artifacts (not in REPO_ARTIFACT_LINEAGE_ORDER).
    REPO_ANALYSIS_LLM_RAW = "REPO_ANALYSIS_LLM_RAW"
    REPO_ANALYSIS_RETRY_RAW = "REPO_ANALYSIS_RETRY_RAW"
    REPO_ANALYSIS_PROMPT = "REPO_ANALYSIS_PROMPT"
    PARSE_ERROR = "PARSE_ERROR"
    GENERATED_TESTS = "GENERATED_TESTS"
    BUG_REPORT = "BUG_REPORT"
    # Supplementary bug-detection artifacts (not in REPO_ARTIFACT_LINEAGE_ORDER).
    BUG_REPORT_LLM_RAW = "BUG_REPORT_LLM_RAW"
    BUG_REPORT_RETRY_RAW = "BUG_REPORT_RETRY_RAW"
    BUG_REPORT_PROMPT = "BUG_REPORT_PROMPT"
    PATCH = "PATCH"
    TEST_RERUN_RESULT = "TEST_RERUN_RESULT"
    EVIDENCE_REPORT = "EVIDENCE_REPORT"


# Lineage order for replay and provenance reconstruction.
# Used by: replay/replay_service.py, orchestrator/provenance_manager.py
ARTIFACT_LINEAGE_ORDER: list[ArtifactType] = [
    ArtifactType.PLAN,
    ArtifactType.SOURCE_CODE,
    ArtifactType.DEBUG_REPORT,
    ArtifactType.FIXED_CODE,
    ArtifactType.FINAL_REPORT,
]

REPO_ARTIFACT_LINEAGE_ORDER: list[ArtifactType] = [
    ArtifactType.ISSUE,
    ArtifactType.REPO_ANALYSIS,
    ArtifactType.GENERATED_TESTS,
    ArtifactType.BUG_REPORT,
    ArtifactType.PATCH,
    ArtifactType.TEST_RERUN_RESULT,
    ArtifactType.EVIDENCE_REPORT,
]

REPO_COMPLETION_SUBJECTS: frozenset[str] = frozenset({
    NatsSubject.REPO_ANALYSIS_COMPLETED.value,
    NatsSubject.REPO_TESTS_GENERATED.value,
    NatsSubject.REPO_BUGS_DETECTED.value,
    NatsSubject.REPO_PATCH_GENERATED.value,
    NatsSubject.REPO_TESTS_RERUN.value,
    NatsSubject.REPO_EVIDENCE_REPORTED.value,
})

REPO_PRIMARY_SUBJECTS: frozenset[str] = frozenset({
    NatsSubject.REPO_WORKFLOW_START.value,
    NatsSubject.REPO_ANALYSIS_COMPLETED.value,
    NatsSubject.REPO_TESTS_GENERATED.value,
    NatsSubject.REPO_BUGS_DETECTED.value,
    NatsSubject.REPO_PATCH_GENERATED.value,
    NatsSubject.REPO_TESTS_RERUN.value,
    NatsSubject.REPO_EVIDENCE_REPORTED.value,
})

# ---------------------------------------------------------------------------
# Error Codes
# ---------------------------------------------------------------------------


class ErrorCode(StrEnum):
    """Structured error codes for failure handling and replay reports."""

    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    TASK_TIMEOUT = "TASK_TIMEOUT"
    TASK_FAILED = "TASK_FAILED"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    MESSAGE_PERSISTENCE_ERROR = "MESSAGE_PERSISTENCE_ERROR"
    WORKFLOW_ABORTED = "WORKFLOW_ABORTED"


# ---------------------------------------------------------------------------
# Retry & Timeout Defaults
# ---------------------------------------------------------------------------

# RetryManager / TimeoutMonitor / BaseAgent heartbeat defaults
DEFAULT_MAX_RETRIES: int = 3
DEFAULT_RETRY_BASE_DELAY_SEC: float = 1.0
DEFAULT_TASK_TIMEOUT_SEC: float = 120.0

# ---------------------------------------------------------------------------
# Langfuse Span Names
# ---------------------------------------------------------------------------

# ObservabilityService trace hierarchy span names
LANGFUSE_SPAN_WORKFLOW: str = "workflow"
LANGFUSE_SPAN_PLANNER: str = "PlannerAgent"
LANGFUSE_SPAN_CODE_GENERATOR: str = "CodeGeneratorAgent"
LANGFUSE_SPAN_DEBUGGER: str = "DebuggerAgent"
LANGFUSE_SPAN_PYTHON_RUNNER: str = "PythonRunner"
LANGFUSE_SPAN_HUMAN_APPROVAL: str = "HumanApproval"
LANGFUSE_SPAN_FIXER_REPORTER: str = "FixerReporterAgent"
LANGFUSE_SPAN_REPO_ANALYZER: str = "RepoAnalyzerAgent"
LANGFUSE_SPAN_TEST_GENERATOR: str = "TestGeneratorAgent"
LANGFUSE_SPAN_BUG_DETECTOR: str = "BugDetectorAgent"
LANGFUSE_SPAN_PATCH_AGENT: str = "PatchAgent"
LANGFUSE_SPAN_TEST_RERUN: str = "TestRerunAgent"
LANGFUSE_SPAN_EVIDENCE_REPORT: str = "EvidenceReportAgent"

# ---------------------------------------------------------------------------
# Demo Workflow
# ---------------------------------------------------------------------------

# Fibonacci demo workflow identifier template — used when starting demo.
DEMO_WORKFLOW_NAME: str = "fibonacci_up_to_100"
REPO_AUDIT_WORKFLOW_NAME: str = "repo_audit_fastapi_demo"

# Human approval prompt shown after DebuggerAgent — orchestrator CLI.
HUMAN_APPROVAL_PROMPT: str = (
    "Debugger found an off-by-one bug.\n\nApprove automatic fix?\n\n"
    "[y] Approve\n[n] Reject"
)

REPO_HUMAN_APPROVAL_PROMPT: str = (
    "BugDetectorAgent found security and correctness issues in the target repo.\n\n"
    "Approve PatchAgent to generate fixes?\n\n"
    "[y] Approve\n[n] Reject"
)

# ---------------------------------------------------------------------------
# Deterministic Metrics Placeholders
# ---------------------------------------------------------------------------

# Placeholder token counts for MockLLMProvider metrics — no real LLM in MVP.
MOCK_TOKENS_IN: int = 128
MOCK_TOKENS_OUT: int = 256

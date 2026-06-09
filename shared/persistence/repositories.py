"""
Repository layer for PostgreSQL persistence.

Centralizes CRUD for replay storage, lineage creation, and idempotency checks.
Imported via shared.persistence.session_provider.SessionProvider.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.constants import TaskState, WorkflowState
from shared.persistence.models import (
    AgentRecord,
    ArtifactRecord,
    HumanApprovalRecord,
    KIOCapabilityRecord,
    MessageRecord,
    MetricRecord,
    ReportRecord,
    TaskRecord,
    UserRecord,
    WorkflowRecord,
    WorkflowSpanRecord,
)


class Repository:
    """Async repository for all System of Record entities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    async def message_exists(self, idempotency_key: str) -> bool:
        result = await self._session.execute(
            select(MessageRecord.id).where(MessageRecord.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none() is not None

    async def save_message(self, envelope: dict[str, Any]) -> MessageRecord:
        record = MessageRecord(
            message_id=envelope["message_id"],
            workflow_id=envelope.get("workflow_id"),
            correlation_id=envelope["correlation_id"],
            causation_id=envelope.get("causation_id"),
            subject=envelope["subject"],
            message_type=envelope["message_type"],
            sender=envelope["sender"],
            recipient=envelope["recipient"],
            payload=envelope.get("payload", {}),
            envelope=envelope,
            timestamp=datetime.fromisoformat(envelope["timestamp"].replace("Z", "+00:00")),
            idempotency_key=envelope["idempotency_key"],
        )
        self._session.add(record)
        await self._session.flush()
        return record

    # ------------------------------------------------------------------
    # Workflows
    # ------------------------------------------------------------------

    async def create_workflow(
        self,
        *,
        name: str,
        project_id: str,
        correlation_id: str,
        session_id: str | None = None,
        owner: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowRecord:
        workflow = WorkflowRecord(
            name=name,
            project_id=project_id,
            state=WorkflowState.CREATED.value,
            correlation_id=correlation_id,
            session_id=session_id,
            owner=owner,
            trace_id=trace_id,
            metadata_=metadata or {},
        )
        self._session.add(workflow)
        await self._session.flush()
        return workflow

    async def get_workflow(self, workflow_id: str) -> WorkflowRecord | None:
        return await self._session.get(WorkflowRecord, workflow_id)

    async def update_workflow_state(
        self,
        workflow_id: str,
        state: WorkflowState,
        *,
        started: bool = False,
        completed: bool = False,
    ) -> WorkflowRecord | None:
        workflow = await self.get_workflow(workflow_id)
        if workflow is None:
            return None
        workflow.state = state.value
        now = datetime.now(timezone.utc)
        if started and workflow.started_at is None:
            workflow.started_at = now
        if completed:
            workflow.completed_at = now
        await self._session.flush()
        return workflow

    async def list_workflows(self, owner: str | None = None) -> list[WorkflowRecord]:
        query = select(WorkflowRecord).order_by(WorkflowRecord.created_at.desc())
        if owner:
            query = query.where(WorkflowRecord.owner == owner)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    async def create_task(
        self,
        *,
        workflow_id: str,
        step_id: str,
        agent_role: str,
        idempotency_key: str,
        kio_id: str | None = None,
    ) -> TaskRecord:
        task = TaskRecord(
            workflow_id=workflow_id,
            step_id=step_id,
            agent_role=agent_role,
            state=TaskState.CREATED.value,
            idempotency_key=idempotency_key,
            kio_id=kio_id,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return await self._session.get(TaskRecord, task_id)

    async def update_task_state(
        self,
        task_id: str,
        state: TaskState,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        increment_retry: bool = False,
    ) -> TaskRecord | None:
        task = await self.get_task(task_id)
        if task is None:
            return None
        task.state = state.value
        now = datetime.now(timezone.utc)
        if state == TaskState.RUNNING and task.started_at is None:
            task.started_at = now
        if state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.REJECTED):
            task.completed_at = now
        if error_code:
            task.error_code = error_code
        if error_message:
            task.error_message = error_message
        if increment_retry:
            task.retry_count += 1
        await self._session.flush()
        return task

    async def list_tasks_for_workflow(self, workflow_id: str) -> list[TaskRecord]:
        result = await self._session.execute(
            select(TaskRecord)
            .where(TaskRecord.workflow_id == workflow_id)
            .order_by(TaskRecord.created_at)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Artifacts & lineage
    # ------------------------------------------------------------------

    @staticmethod
    def _checksum(content: dict[str, Any]) -> str:
        serialized = json.dumps(content, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()

    async def create_artifact(
        self,
        *,
        workflow_id: str,
        artifact_type: str,
        content: dict[str, Any],
        artifact_id: str | None = None,
        task_id: str | None = None,
        kio_id: str | None = None,
        parent_artifact_id: str | None = None,
    ) -> ArtifactRecord:
        kwargs: dict[str, Any] = dict(
            workflow_id=workflow_id,
            task_id=task_id,
            kio_id=kio_id,
            artifact_type=artifact_type,
            parent_artifact_id=parent_artifact_id,
            content=content,
            checksum=self._checksum(content),
        )
        # 4.4: use caller-supplied UUID as PK so artifact_id == DB PK and FK lineage works
        if artifact_id:
            kwargs["id"] = artifact_id
        artifact = ArtifactRecord(**kwargs)
        self._session.add(artifact)
        await self._session.flush()
        return artifact

    async def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        return await self._session.get(ArtifactRecord, artifact_id)

    async def list_artifacts_for_workflow(self, workflow_id: str) -> list[ArtifactRecord]:
        result = await self._session.execute(
            select(ArtifactRecord)
            .where(ArtifactRecord.workflow_id == workflow_id)
            .order_by(ArtifactRecord.created_at)
        )
        return list(result.scalars().all())

    async def get_latest_artifact_by_type(
        self, workflow_id: str, artifact_type: str
    ) -> ArtifactRecord | None:
        result = await self._session.execute(
            select(ArtifactRecord)
            .where(
                ArtifactRecord.workflow_id == workflow_id,
                ArtifactRecord.artifact_type == artifact_type,
            )
            .order_by(ArtifactRecord.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Human Approvals (HITL)
    # ------------------------------------------------------------------

    async def create_approval_request(
        self,
        *,
        workflow_id: str,
        task_id: str,
        prompt: str,
        kio_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HumanApprovalRecord:
        approval = HumanApprovalRecord(
            workflow_id=workflow_id,
            task_id=task_id,
            kio_id=kio_id,
            prompt=prompt,
            metadata_=metadata or {},
        )
        self._session.add(approval)
        await self._session.flush()
        return approval

    async def resolve_approval(
        self,
        approval_id: str,
        *,
        decision: str,
        feedback: str | None = None,
        decided_by: str = "human",
    ) -> HumanApprovalRecord | None:
        approval = await self._session.get(HumanApprovalRecord, approval_id)
        if approval is None:
            return None
        approval.decision = decision
        approval.feedback = feedback
        approval.decided_by = decided_by
        approval.decided_at = datetime.now(timezone.utc)
        await self._session.flush()
        return approval

    async def get_approval(self, approval_id: str) -> HumanApprovalRecord | None:
        """Fetch a single HumanApprovalRecord by primary key."""
        return await self._session.get(HumanApprovalRecord, approval_id)

    async def list_approvals_for_workflow(self, workflow_id: str) -> list[HumanApprovalRecord]:
        result = await self._session.execute(
            select(HumanApprovalRecord)
            .where(HumanApprovalRecord.workflow_id == workflow_id)
            .order_by(HumanApprovalRecord.created_at)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # KIO Capability Registry
    # ------------------------------------------------------------------

    async def upsert_kio_capability(
        self,
        *,
        kio_id: str,
        name: str,
        description: str,
        output_artifact_type: str,
        input_artifact_type: str | None = None,
        version: str = "1.0.0",
        tags: list[str] | None = None,
        available: bool = True,
        port: int | None = None,
    ) -> KIOCapabilityRecord:
        result = await self._session.execute(
            select(KIOCapabilityRecord).where(KIOCapabilityRecord.kio_id == kio_id)
        )
        existing = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if existing:
            existing.name = name
            existing.description = description
            existing.output_artifact_type = output_artifact_type
            existing.input_artifact_type = input_artifact_type
            existing.version = version
            existing.tags = tags or []
            existing.available = available
            existing.port = port
            existing.updated_at = now
            await self._session.flush()
            return existing
        record = KIOCapabilityRecord(
            kio_id=kio_id,
            name=name,
            description=description,
            output_artifact_type=output_artifact_type,
            input_artifact_type=input_artifact_type,
            version=version,
            tags=tags or [],
            available=available,
            port=port,
            updated_at=now,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_kio_capabilities(
        self, available_only: bool = True
    ) -> list[KIOCapabilityRecord]:
        query = select(KIOCapabilityRecord).order_by(KIOCapabilityRecord.kio_id)
        if available_only:
            query = query.where(KIOCapabilityRecord.available.is_(True))
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_kio_capability(self, kio_id: str) -> KIOCapabilityRecord | None:
        result = await self._session.execute(
            select(KIOCapabilityRecord).where(KIOCapabilityRecord.kio_id == kio_id)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Metrics, spans, reports, messages
    # ------------------------------------------------------------------

    async def record_metric(
        self,
        *,
        workflow_id: str,
        metric_name: str,
        metric_value: float,
        task_id: str | None = None,
        kio_id: str | None = None,
        unit: str = "ms",
    ) -> MetricRecord:
        metric = MetricRecord(
            workflow_id=workflow_id,
            task_id=task_id,
            kio_id=kio_id,
            metric_name=metric_name,
            metric_value=metric_value,
            unit=unit,
        )
        self._session.add(metric)
        await self._session.flush()
        return metric

    async def record_metric_once(
        self,
        *,
        workflow_id: str,
        metric_name: str,
        metric_value: float,
        task_id: str | None = None,
        unit: str = "ms",
    ) -> MetricRecord | None:
        query = select(MetricRecord.id).where(
            MetricRecord.workflow_id == workflow_id,
            MetricRecord.metric_name == metric_name,
        )
        if task_id:
            query = query.where(MetricRecord.task_id == task_id)
        result = await self._session.execute(query)
        if result.scalar_one_or_none() is not None:
            return None
        return await self.record_metric(
            workflow_id=workflow_id,
            metric_name=metric_name,
            metric_value=metric_value,
            task_id=task_id,
            unit=unit,
        )

    async def record_metric_add(
        self,
        *,
        workflow_id: str,
        metric_name: str,
        delta: float,
        task_id: str | None = None,
        unit: str = "ms",
    ) -> MetricRecord:
        query = select(MetricRecord).where(
            MetricRecord.workflow_id == workflow_id,
            MetricRecord.metric_name == metric_name,
        )
        if task_id:
            query = query.where(MetricRecord.task_id == task_id)
        else:
            query = query.where(MetricRecord.task_id.is_(None))
        result = await self._session.execute(query)
        existing = result.scalar_one_or_none()
        if existing:
            existing.metric_value += delta
            await self._session.flush()
            return existing
        return await self.record_metric(
            workflow_id=workflow_id,
            metric_name=metric_name,
            metric_value=delta,
            task_id=task_id,
            unit=unit,
        )

    async def record_span(
        self,
        *,
        workflow_id: str,
        span_name: str,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowSpanRecord:
        span = WorkflowSpanRecord(
            workflow_id=workflow_id,
            span_name=span_name,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            metadata_=metadata or {},
        )
        self._session.add(span)
        await self._session.flush()
        return span

    async def create_report(
        self,
        *,
        workflow_id: str,
        report_type: str,
        content: dict[str, Any],
    ) -> ReportRecord:
        report = ReportRecord(
            workflow_id=workflow_id,
            report_type=report_type,
            content=content,
        )
        self._session.add(report)
        await self._session.flush()
        return report

    async def list_messages_for_workflow(self, workflow_id: str) -> list[MessageRecord]:
        result = await self._session.execute(
            select(MessageRecord)
            .where(MessageRecord.workflow_id == workflow_id)
            .order_by(MessageRecord.timestamp)
        )
        return list(result.scalars().all())

    async def list_metrics_for_workflow(self, workflow_id: str) -> list[MetricRecord]:
        result = await self._session.execute(
            select(MetricRecord)
            .where(MetricRecord.workflow_id == workflow_id)
            .order_by(MetricRecord.recorded_at)
        )
        return list(result.scalars().all())

    async def list_spans_for_workflow(self, workflow_id: str) -> list[WorkflowSpanRecord]:
        result = await self._session.execute(
            select(WorkflowSpanRecord)
            .where(WorkflowSpanRecord.workflow_id == workflow_id)
            .order_by(WorkflowSpanRecord.started_at)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    async def register_agent(
        self,
        *,
        kio_id: str,
        name: str,
        role: str,
        capabilities: dict[str, Any],
        port: int | None = None,
    ) -> AgentRecord:
        """Upsert KIO agent — keyed on kio_id (unique service identity)."""
        result = await self._session.execute(
            select(AgentRecord).where(AgentRecord.kio_id == kio_id)
        )
        existing = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if existing:
            existing.name = name
            existing.role = role
            existing.capabilities = capabilities
            existing.port = port
            existing.last_heartbeat_at = now
            await self._session.flush()
            return existing
        agent = AgentRecord(
            kio_id=kio_id,
            name=name,
            role=role,
            capabilities=capabilities,
            port=port,
            last_heartbeat_at=now,
        )
        self._session.add(agent)
        await self._session.flush()
        return agent

    async def update_agent_heartbeat(self, kio_id: str) -> None:
        """Stamp last_heartbeat_at — called by KIO health-check loop."""
        result = await self._session.execute(
            select(AgentRecord).where(AgentRecord.kio_id == kio_id)
        )
        agent = result.scalar_one_or_none()
        if agent:
            agent.last_heartbeat_at = datetime.now(timezone.utc)
            await self._session.flush()

    async def list_agents(self, available_only: bool = False) -> list[AgentRecord]:
        """List registered KIO agents."""
        query = select(AgentRecord).order_by(AgentRecord.kio_id)
        if available_only:
            query = query.where(AgentRecord.available.is_(True))
        result = await self._session.execute(query)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def create_user(
        self, *, username: str, hashed_password: str, email: str | None = None
    ) -> UserRecord:
        """Insert a new user row; raises IntegrityError on duplicate username/email."""
        user = UserRecord(username=username, hashed_password=hashed_password, email=email)
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        """Look up a user by username for login/token validation."""
        result = await self._session.execute(
            select(UserRecord).where(UserRecord.username == username)
        )
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: str) -> UserRecord | None:
        """Fetch a user by primary key (used by token validation)."""
        return await self._session.get(UserRecord, user_id)

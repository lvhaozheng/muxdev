"""Conversation-native direct work and explicit multi-agent orchestration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from ..models import (
    AssignmentStatus,
    ConversationMode,
    OrchestrationPlanStatus,
    OrchestrationPlanV1,
)
from ..models.evidence import canonical_hash
from ..services.agents import AgentRegistry
from ..storage import ControlStore
from .agent_sessions import AgentSessionManager, agent_session_manager
from .conversation_service import ConversationService
from .workspace import (
    ChangeSet,
    WorkspaceConflictError,
    apply_change_set,
    build_change_set,
    snapshot_workspace,
)
from .worktree import WorktreeManager


_MENTION = re.compile(r"(?:^|\s)@([A-Za-z0-9][A-Za-z0-9_-]{0,79})")
_ACTIVE_ASSIGNMENT_STATES = {
    AssignmentStatus.QUEUED.value,
    AssignmentStatus.RUNNING.value,
    AssignmentStatus.WAITING_USER.value,
    AssignmentStatus.REPORTED.value,
    AssignmentStatus.VERIFYING.value,
    AssignmentStatus.READY_TO_MERGE.value,
    AssignmentStatus.MERGING.value,
}


@dataclass
class CollaborationService:
    conversations: ConversationService
    store: ControlStore

    def __post_init__(self) -> None:
        self.workspace = self.conversations.workspace
        self.registry = AgentRegistry(self.workspace)
        self.sessions: AgentSessionManager = agent_session_manager(self.workspace)

    def create(
        self,
        goal: str,
        *,
        mode: str = ConversationMode.DIRECT.value,
        agent_id: str | None = None,
        orchestrator_agent_id: str | None = None,
        title: str | None = None,
        acceptance_criteria: list[str] | None = None,
        allowed_scope: list[str] | None = None,
        profile: str = "standard",
        delivery_standard: Mapping[str, object] | None = None,
        max_cost_usd: float = 0.5,
        max_parallel: int = 4,
    ) -> dict[str, Any]:
        parsed_mode = ConversationMode(mode)
        if parsed_mode == ConversationMode.LEGACY_PIPELINE:
            return self.conversations.create(
                goal,
                title=title,
                acceptance_criteria=acceptance_criteria,
                allowed_scope=allowed_scope,
                profile=profile,
                provider=agent_id or "mock",
                max_cost_usd=max_cost_usd,
            )
        max_parallel = max(1, min(int(max_parallel), 4))
        primary_id = agent_id or (orchestrator_agent_id if parsed_mode == ConversationMode.ORCHESTRATED else "mock")
        primary = self.registry.get(str(primary_id))
        orchestrator = None
        if parsed_mode == ConversationMode.ORCHESTRATED:
            orchestrator = self.registry.get(orchestrator_agent_id or primary.agent_id)
            if not orchestrator.can_orchestrate:
                raise ValueError(f"agent cannot orchestrate: {orchestrator.agent_id}")
        detail = self.conversations.create(
            goal,
            title=title,
            acceptance_criteria=acceptance_criteria,
            allowed_scope=allowed_scope,
            profile=profile,
            provider=primary.agent_id,
            max_cost_usd=max_cost_usd,
        )
        conversation_id = str(detail["conversation"]["conversation_id"])
        if delivery_standard is not None:
            detail = self.conversations.revise_contract(
                conversation_id, {"delivery_standard": dict(delivery_standard)}
            )
        self.store.update_conversation(
            conversation_id,
            mode=parsed_mode.value,
            primary_agent_id=primary.agent_id,
            orchestrator_agent_id=orchestrator.agent_id if orchestrator else None,
            metadata={"max_parallel": max_parallel, "cli_native": True},
        )
        self.store.append_conversation_event(
            conversation_id,
            "conversation.mode_selected",
            {
                "mode": parsed_mode.value,
                "primary_agent_id": primary.agent_id,
                "orchestrator_agent_id": orchestrator.agent_id if orchestrator else None,
                "max_parallel": max_parallel,
            },
            actor="developer",
        )
        contract = self._active_contract(conversation_id)
        if parsed_mode == ConversationMode.DIRECT:
            assignment = self._create_assignment(
                conversation_id=conversation_id,
                agent_id=primary.agent_id,
                dispatch_kind="direct",
                work_mode="write",
                status=AssignmentStatus.QUEUED.value,
                title=str(detail["conversation"]["title"]),
                brief=goal,
                allowed_scope=list(contract["allowed_scope"]),
                deliverables=["完成任务目标并报告改动内容"],
                completion=list(contract["acceptance_criteria"]),
                proof=["内容寻址 ChangeSet", "Runtime 确定性检查", "独立交付评审"],
            )
            self._start_assignment(assignment)
        else:
            assignment = self._create_assignment(
                conversation_id=conversation_id,
                agent_id=orchestrator.agent_id,
                dispatch_kind="orchestrate",
                work_mode="consult",
                status=AssignmentStatus.QUEUED.value,
                title="提出多 Agent 编排计划",
                brief=goal,
                allowed_scope=list(contract["allowed_scope"]),
                deliverables=["可确认的 OrchestrationPlanV1"],
                completion=["计划无环、覆盖任务契约并显式分配 Agent"],
                proof=["Runtime 校验通过的计划版本"],
            )
            self._start_assignment(assignment)
        return self.get(conversation_id)

    def get(self, conversation_id: str) -> dict[str, Any]:
        detail = self.conversations.get(conversation_id)
        conversation = detail["conversation"]
        detail.update(
            {
                "participants": self._participants(conversation_id),
                "plans": self.store.list_orchestration_plans(conversation_id),
                "assignments": self._assignments_with_dependencies(conversation_id),
                "sessions": self.store.list_agent_sessions(conversation_id),
                "timeline": self.store.conversation_events(conversation_id),
                "mode": conversation.get("mode", ConversationMode.LEGACY_PIPELINE.value),
            }
        )
        return detail

    def add_message(
        self,
        conversation_id: str,
        content: str,
        *,
        recipients: Sequence[str] | None = None,
        dispatch_kind: str | None = None,
    ) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        content = content.strip()
        if not content:
            raise ValueError("message content is required")
        requested = list(dict.fromkeys([*(recipients or []), *_MENTION.findall(content)]))
        if not requested:
            requested = [str(conversation.get("primary_agent_id") or "")]
        requested = [item for item in requested if item]
        event_id = self.store.append_conversation_event(
            conversation_id,
            "user.message",
            {
                "content": content,
                "recipients": requested,
                "dispatch_kind": dispatch_kind or "message",
            },
            actor="developer",
        )
        routed: list[dict[str, str]] = []
        for recipient in requested:
            self.registry.get(recipient)
            active = next(
                (
                    item for item in reversed(self.store.list_assignments(conversation_id))
                    if item["agent_id"] == recipient and item["status"] in _ACTIVE_ASSIGNMENT_STATES
                ),
                None,
            )
            if active and dispatch_kind not in {"consult", "write"}:
                session = self._session_for_assignment(str(active["assignment_id"]))
                if session:
                    self.sessions.send_runtime(str(session["session_id"]), content)
                    routed.append({"agent_id": recipient, "assignment_id": str(active["assignment_id"])})
                    continue
            kind = dispatch_kind or "consult"
            assignment = self.dispatch(
                conversation_id,
                {
                    "agent_id": recipient,
                    "dispatch_kind": kind,
                    "work_mode": "write" if kind == "write" else "consult",
                    "title": f"定向协作：{content[:60]}",
                    "brief": content,
                    "deliverables": ["面向发起者的结构化回报"],
                    "completion": ["回答定向问题或完成声明范围"],
                    "proof": ["Agent report manifest"],
                },
            )
            routed.append({"agent_id": recipient, "assignment_id": str(assignment["assignment_id"])})
        return {"event_id": event_id, "recipients": routed}

    def propose_plan(
        self,
        conversation_id: str,
        value: Mapping[str, object],
        *,
        orchestrator_agent_id: str | None = None,
    ) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        if conversation.get("mode") != ConversationMode.ORCHESTRATED.value:
            raise ValueError("orchestration plans require an orchestrated conversation")
        expected = str(conversation.get("orchestrator_agent_id") or "")
        actor = orchestrator_agent_id or expected
        if actor != expected:
            raise PermissionError("only the selected orchestrator may propose this plan")
        orchestrator = self.registry.get(actor)
        if not orchestrator.can_orchestrate:
            raise ValueError(f"agent cannot orchestrate: {actor}")
        plan = OrchestrationPlanV1.model_validate(value)
        for node in plan.nodes:
            self.registry.get(node.agent_id)
        existing = self.store.list_orchestration_plans(conversation_id)
        for previous in existing:
            if previous["status"] in {
                OrchestrationPlanStatus.DRAFT.value,
                OrchestrationPlanStatus.AWAITING_APPROVAL.value,
            }:
                self.store.update_orchestration_plan(
                    str(previous["plan_id"]), status=OrchestrationPlanStatus.SUPERSEDED.value
                )
        version = max((int(item["version"]) for item in existing), default=0) + 1
        contract = self._active_contract(conversation_id)
        scope_digest = canonical_hash(
            {
                "goal": contract["goal"],
                "allowed_scope": contract["allowed_scope"],
                "acceptance_criteria": contract["acceptance_criteria"],
                "max_cost_usd": contract["max_cost_usd"],
                "max_parallel": min(plan.max_parallel, int((conversation.get("metadata") or {}).get("max_parallel", 4))),
            }
        )
        plan_id = f"plan_{uuid4().hex}"
        stored = self.store.create_orchestration_plan(
            plan_id=plan_id,
            conversation_id=conversation_id,
            version=version,
            status=OrchestrationPlanStatus.AWAITING_APPROVAL.value,
            orchestrator_agent_id=actor,
            summary=plan.summary,
            plan=plan.model_dump(mode="json"),
            scope_digest=scope_digest,
            metadata={"frozen_contract_id": contract["contract_id"]},
        )
        node_assignments: dict[str, dict[str, Any]] = {}
        for node in plan.nodes:
            node_assignments[node.id] = self._create_assignment(
                conversation_id=conversation_id,
                plan_id=plan_id,
                node_id=node.id,
                agent_id=node.agent_id,
                dispatch_kind="orchestrated",
                work_mode=node.work_mode,
                status=AssignmentStatus.PROPOSED.value,
                title=node.title,
                brief=node.brief,
                allowed_scope=list(contract["allowed_scope"]),
                deliverables=node.deliverables,
                completion=node.completion,
                proof=node.proof,
                metadata={"role": node.role, "plan_version": version},
            )
        for node in plan.nodes:
            self.store.add_assignment_dependencies(
                str(node_assignments[node.id]["assignment_id"]),
                [str(node_assignments[item]["assignment_id"]) for item in node.dependencies],
            )
        self.store.update_conversation(
            conversation_id,
            status="needs_user",
            active_plan_id=plan_id,
        )
        self.store.append_conversation_event(
            conversation_id,
            "orchestration.plan_proposed",
            {"plan_id": plan_id, "version": version, "plan": plan.model_dump(mode="json")},
            actor=actor,
        )
        return stored

    def approve_plan(self, conversation_id: str, plan_id: str) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        if str(conversation.get("active_plan_id") or "") != plan_id:
            raise ValueError("only the active orchestration plan can be approved")
        plan = self.store.get_orchestration_plan(plan_id)
        if not plan or plan["status"] != OrchestrationPlanStatus.AWAITING_APPROVAL.value:
            raise ValueError("orchestration plan is not awaiting approval")
        approved_at = self._now()
        self.store.update_orchestration_plan(
            plan_id,
            status=OrchestrationPlanStatus.RUNNING.value,
            approved_at=approved_at,
            metadata={"frozen": True},
        )
        for assignment in self.store.list_assignments(conversation_id):
            if assignment.get("plan_id") == plan_id and assignment["status"] == AssignmentStatus.PROPOSED.value:
                self.store.update_assignment(
                    str(assignment["assignment_id"]), status=AssignmentStatus.QUEUED.value
                )
        self.store.update_conversation(conversation_id, status="working")
        self.store.append_conversation_event(
            conversation_id,
            "orchestration.plan_approved",
            {"plan_id": plan_id, "version": plan["version"], "approved_at": approved_at},
            actor="developer",
        )
        self._schedule(conversation_id, plan_id)
        return self.get(conversation_id)

    def revise_plan(
        self,
        conversation_id: str,
        value: Mapping[str, object],
        *,
        orchestrator_agent_id: str | None = None,
    ) -> dict[str, Any]:
        current = self._active_plan(conversation_id)
        revised = OrchestrationPlanV1.model_validate(value)
        old = OrchestrationPlanV1.model_validate(current["plan"])
        boundary_expansion = self._plan_expands_boundary(old, revised)
        stored = self.propose_plan(
            conversation_id,
            revised.model_dump(mode="json"),
            orchestrator_agent_id=orchestrator_agent_id,
        )
        if not boundary_expansion and current["status"] in {
            OrchestrationPlanStatus.APPROVED.value,
            OrchestrationPlanStatus.RUNNING.value,
        }:
            self.store.append_conversation_event(
                conversation_id,
                "orchestration.plan_revised_in_scope",
                {"from_plan_id": current["plan_id"], "to_plan_id": stored["plan_id"]},
                actor=str(orchestrator_agent_id or current["orchestrator_agent_id"]),
            )
            return self.approve_plan(conversation_id, str(stored["plan_id"]))
        return {**stored, "requires_confirmation": True, "boundary_expansion": boundary_expansion}

    def dispatch(
        self,
        conversation_id: str,
        value: Mapping[str, object],
        *,
        parent_assignment_id: str | None = None,
    ) -> dict[str, Any]:
        contract = self._active_contract(conversation_id)
        agent_id = str(value.get("agent_id") or "")
        self.registry.get(agent_id)
        work_mode = str(value.get("work_mode") or "consult")
        if work_mode not in {"consult", "write"}:
            raise ValueError("assignment work_mode must be consult or write")
        requested_scope = list(value.get("allowed_scope") or contract["allowed_scope"])
        if parent_assignment_id:
            parent = self._assignment(parent_assignment_id)
            if str(parent["conversation_id"]) != conversation_id:
                raise PermissionError("parent assignment belongs to another conversation")
            if not self._scope_is_subset(requested_scope, list(parent["allowed_scope"])):
                raise PermissionError("child assignment cannot expand its parent scope")
        assignment = self._create_assignment(
            conversation_id=conversation_id,
            parent_assignment_id=parent_assignment_id,
            agent_id=agent_id,
            dispatch_kind=str(value.get("dispatch_kind") or "manual"),
            work_mode=work_mode,
            status=AssignmentStatus.QUEUED.value,
            title=str(value.get("title") or "Manual assignment"),
            brief=str(value.get("brief") or ""),
            allowed_scope=requested_scope,
            deliverables=list(value.get("deliverables") or []),
            completion=list(value.get("completion") or []),
            proof=list(value.get("proof") or []),
        )
        self._start_assignment(assignment)
        return self.store.get_assignment(str(assignment["assignment_id"])) or assignment

    def report(self, assignment_id: str, manifest: Mapping[str, object]) -> dict[str, Any]:
        assignment = self._assignment(assignment_id)
        if assignment["status"] not in {
            AssignmentStatus.RUNNING.value,
            AssignmentStatus.WAITING_USER.value,
        }:
            raise ValueError("only running assignments can report")
        required = ("summary", "deliverables", "proof")
        missing = [key for key in required if not manifest.get(key)]
        if missing:
            raise ValueError(f"assignment report is missing: {', '.join(missing)}")
        if assignment["dispatch_kind"] in {"review", "security-review"}:
            review_status = str(manifest.get("status") or "")
            if review_status not in {"passed", "failed", "satisfied", "blocked"}:
                raise ValueError("review reports require status passed, satisfied, failed, or blocked")
        now = self._now()
        assignment = self.store.update_assignment(
            assignment_id,
            status=AssignmentStatus.REPORTED.value,
            reported_at=now,
            metadata={"report": dict(manifest)},
        )
        self.store.append_conversation_event(
            str(assignment["conversation_id"]),
            "assignment.reported",
            {
                "assignment_id": assignment_id,
                "agent_id": assignment["agent_id"],
                "summary": str(manifest["summary"]),
                "deliverables": manifest["deliverables"],
                "proof": manifest["proof"],
            },
            actor=str(assignment["agent_id"]),
        )
        if assignment["work_mode"] == "write" and assignment["dispatch_kind"] != "direct":
            assignment = self._prepare_changeset(assignment)
        else:
            assignment = self.store.update_assignment(
                assignment_id,
                status=AssignmentStatus.COMPLETED.value,
                completed_at=now,
            )
        conversation_id = str(assignment["conversation_id"])
        if assignment.get("plan_id"):
            self._drain_merges(conversation_id, str(assignment["plan_id"]))
            self._schedule(conversation_id, str(assignment["plan_id"]))
        self._ensure_reviewers_or_finalize(conversation_id)
        return self.store.get_assignment(assignment_id) or assignment

    def retry(self, assignment_id: str) -> dict[str, Any]:
        assignment = self._assignment(assignment_id)
        attempts = int(assignment.get("recovery_attempts") or 0)
        if attempts >= 2:
            return self.store.update_assignment(
                assignment_id,
                status=AssignmentStatus.BLOCKED.value,
                metadata={"blocked_reason": "safe recovery limit reached"},
            )
        updated = self.store.update_assignment(
            assignment_id,
            status=AssignmentStatus.QUEUED.value,
            recovery_attempts=attempts + 1,
        )
        self._start_assignment(updated, recovery=True)
        return self.store.get_assignment(assignment_id) or updated

    def reassign(self, assignment_id: str, agent_id: str) -> dict[str, Any]:
        self.registry.get(agent_id)
        assignment = self._assignment(assignment_id)
        if assignment["status"] in {AssignmentStatus.COMPLETED.value, AssignmentStatus.CANCELLED.value}:
            raise ValueError("completed or cancelled assignments cannot be reassigned")
        updated = self.store.update_assignment(
            assignment_id,
            agent_id=agent_id,
            status=AssignmentStatus.QUEUED.value,
            metadata={"reassigned_from": assignment["agent_id"]},
        )
        self._start_assignment(updated, recovery=True)
        return self.store.get_assignment(assignment_id) or updated

    def cancel(self, assignment_id: str) -> dict[str, Any]:
        assignment = self._assignment(assignment_id)
        session = self._session_for_assignment(assignment_id)
        if session and session["status"] not in {"closed", "failed"}:
            try:
                self.sessions.close(str(session["session_id"]))
            except RuntimeError:
                pass
        return self.store.update_assignment(
            assignment_id,
            status=AssignmentStatus.CANCELLED.value,
            completed_at=self._now(),
        )

    def authorize_scoped_token(self, raw_token: str, *, assignment_id: str | None = None) -> dict[str, Any]:
        session = self.sessions.authorize_control_token(raw_token)
        if assignment_id and str(session["assignment_id"]) != assignment_id:
            raise PermissionError("control token is scoped to a different assignment")
        return session

    def approve_verification(self, conversation_id: str) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        if conversation["status"] not in {"needs_user", "verifying"}:
            raise ValueError("conversation is not awaiting verification approval")
        self.store.append_conversation_event(
            conversation_id,
            "verification.approved",
            {"contract_id": self._active_contract(conversation_id)["contract_id"]},
            actor="developer",
        )
        return self._finalize_delivery(conversation_id)

    def _create_assignment(self, **values: object) -> dict[str, Any]:
        for field in ("deliverables", "completion", "proof"):
            if not values.get(field):
                raise ValueError(f"assignment requires at least one {field} item")
        assignment_id = f"asn_{uuid4().hex}"
        assignment = self.store.create_assignment(assignment_id=assignment_id, **values)
        self.store.append_conversation_event(
            str(assignment["conversation_id"]),
            "assignment.created",
            {
                "assignment_id": assignment_id,
                "plan_id": assignment.get("plan_id"),
                "agent_id": assignment["agent_id"],
                "title": assignment["title"],
                "work_mode": assignment["work_mode"],
                "status": assignment["status"],
            },
            actor="supervisor",
        )
        return assignment

    def _start_assignment(self, assignment: Mapping[str, Any], *, recovery: bool = False) -> dict[str, Any]:
        assignment_id = str(assignment["assignment_id"])
        conversation_id = str(assignment["conversation_id"])
        conversation = self._conversation(conversation_id)
        integration = Path(str((conversation.get("metadata") or {}).get("worktree") or ""))
        if assignment["dispatch_kind"] == "direct":
            worktree = integration
            strategy = "conversation_integration"
        else:
            assignment_dir = (
                self.workspace / ".muxdev" / "conversations" / conversation_id / "assignments" / assignment_id
            )
            assignment_dir.mkdir(parents=True, exist_ok=True)
            prepared = WorktreeManager(
                integration,
                worktrees_root=assignment_dir.parent / "worktrees",
            ).prepare(assignment_id, assignment_dir)
            worktree, strategy = prepared.path, prepared.strategy
        baseline = snapshot_workspace(worktree)
        updated = self.store.update_assignment(
            assignment_id,
            status=AssignmentStatus.RUNNING.value,
            baseline_digest=baseline.digest,
            worktree=str(worktree),
            metadata={"baseline_manifest": baseline.to_dict(), "worktree_strategy": strategy},
        )
        bootstrap = self._bootstrap(updated)
        session = self._session_for_assignment(assignment_id)
        if recovery and session:
            try:
                self.sessions.close(str(session["session_id"]))
            except (RuntimeError, FileNotFoundError):
                pass
            session = None
        session_record = self.sessions.start(
            conversation_id=conversation_id,
            assignment_id=assignment_id,
            agent_id=str(updated["agent_id"]),
            worktree=worktree,
            bootstrap=bootstrap,
        )
        self.store.append_conversation_event(
            conversation_id,
            "assignment.started",
            {
                "assignment_id": assignment_id,
                "agent_id": updated["agent_id"],
                "session_id": session_record["session_id"],
                "worktree_strategy": strategy,
            },
            actor="supervisor",
        )
        return updated

    def _bootstrap(self, assignment: Mapping[str, Any]) -> str:
        contract = self._active_contract(str(assignment["conversation_id"]))
        dependencies = [
            self._assignment(item)
            for item in self.store.list_assignment_dependencies(str(assignment["assignment_id"]))
        ]
        dependency_outputs = [
            {
                "assignment_id": item["assignment_id"],
                "agent_id": item["agent_id"],
                "report": (item.get("metadata") or {}).get("report"),
                "changeset_digest": item.get("changeset_digest"),
            }
            for item in dependencies
        ]
        payload = {
            "schema": "muxdev.assignment-brief.v1",
            "contract": {
                "goal": contract["goal"],
                "acceptance_criteria": contract["acceptance_criteria"],
                "allowed_scope": contract["allowed_scope"],
                "delivery_standard": (contract.get("policy") or {}).get("delivery_standard"),
            },
            "assignment": {
                key: assignment[key]
                for key in (
                    "assignment_id", "title", "brief", "work_mode", "deliverables",
                    "completion", "proof",
                )
            },
            "dependency_outputs": dependency_outputs,
            "collaboration_commands": [
                "muxdev collab roster",
                "muxdev collab dispatch --file <assignment.json>",
                "muxdev collab send --to <agent-or-assignment>",
                "muxdev collab ask",
                "muxdev collab report --file <report.json>",
                "muxdev collab deliver --manifest <delivery.json>",
            ],
            "security": "The role prompt is not a permission boundary. Runtime scope and delivery gates are authoritative.",
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _prepare_changeset(self, assignment: Mapping[str, Any]) -> dict[str, Any]:
        metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), dict) else {}
        baseline_raw = metadata.get("baseline_manifest")
        if not isinstance(baseline_raw, dict):
            raise RuntimeError("assignment baseline manifest is missing")
        from .workspace import WorkspaceSnapshot

        before = WorkspaceSnapshot.from_dict(baseline_raw)
        worktree = Path(str(assignment["worktree"]))
        after = snapshot_workspace(worktree)
        change_set = build_change_set(before, after)
        self._validate_changeset_scope(change_set, list(assignment["allowed_scope"]))
        assignment_dir = (
            self.workspace / ".muxdev" / "conversations" / str(assignment["conversation_id"])
            / "assignments" / str(assignment["assignment_id"])
        )
        assignment_dir.mkdir(parents=True, exist_ok=True)
        path = assignment_dir / "changeset.json"
        path.write_text(json.dumps(change_set.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        digest = canonical_hash(change_set.to_dict())
        return self.store.update_assignment(
            str(assignment["assignment_id"]),
            status=AssignmentStatus.READY_TO_MERGE.value,
            changeset_digest=digest,
            metadata={"changeset_path": str(path.resolve())},
        )

    def _drain_merges(self, conversation_id: str, plan_id: str) -> None:
        ordered = self._topological_assignments(conversation_id, plan_id)
        for assignment in ordered:
            status = str(assignment["status"])
            if status in {AssignmentStatus.COMPLETED.value, AssignmentStatus.CANCELLED.value}:
                continue
            if assignment["work_mode"] == "consult":
                if status == AssignmentStatus.REPORTED.value:
                    self.store.update_assignment(
                        str(assignment["assignment_id"]),
                        status=AssignmentStatus.COMPLETED.value,
                        completed_at=self._now(),
                    )
                    continue
                return
            if status != AssignmentStatus.READY_TO_MERGE.value:
                return
            self._merge_assignment(assignment)

    def _merge_assignment(self, assignment: Mapping[str, Any]) -> None:
        assignment_id = str(assignment["assignment_id"])
        conversation_id = str(assignment["conversation_id"])
        metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), dict) else {}
        path = Path(str(metadata.get("changeset_path") or ""))
        if not path.is_file():
            raise FileNotFoundError(f"assignment ChangeSet is missing: {path}")
        change_set = ChangeSet.from_dict(json.loads(path.read_text(encoding="utf-8")))
        conversation = self._conversation(conversation_id)
        integration = Path(str((conversation.get("metadata") or {}).get("worktree") or ""))
        self.store.update_assignment(assignment_id, status=AssignmentStatus.MERGING.value)
        try:
            applied = apply_change_set(change_set, Path(str(assignment["worktree"])), integration)
        except WorkspaceConflictError as exc:
            self.store.update_assignment(
                assignment_id,
                status=AssignmentStatus.BLOCKED.value,
                metadata={"merge_conflicts": exc.conflicts},
            )
            resolver = str(conversation.get("orchestrator_agent_id") or conversation.get("primary_agent_id"))
            conflict = self._create_assignment(
                conversation_id=conversation_id,
                parent_assignment_id=assignment_id,
                agent_id=resolver,
                dispatch_kind="conflict_resolution",
                work_mode="write",
                status=AssignmentStatus.QUEUED.value,
                title=f"Resolve merge conflict for {assignment_id}",
                brief="Resolve only the Runtime-reported touched-file conflicts without expanding scope.",
                allowed_scope=list(assignment["allowed_scope"]),
                deliverables=["冲突解决 ChangeSet"],
                completion=["所有触碰文件基线匹配且原 Assignment 交付保持成立"],
                proof=["新的内容寻址 ChangeSet 与确定性检查"],
                metadata={"conflicts": exc.conflicts},
            )
            self._start_assignment(conflict)
            self.store.append_conversation_event(
                conversation_id,
                "assignment.merge_conflict",
                {"assignment_id": assignment_id, "conflicts": exc.conflicts, "resolver_assignment_id": conflict["assignment_id"]},
                actor="supervisor",
            )
            return
        self.store.update_assignment(
            assignment_id,
            status=AssignmentStatus.COMPLETED.value,
            completed_at=self._now(),
            metadata={"merged_paths": applied},
        )
        self.store.append_conversation_event(
            conversation_id,
            "assignment.merged",
            {
                "assignment_id": assignment_id,
                "changeset_digest": assignment["changeset_digest"],
                "paths": applied,
            },
            actor="supervisor",
        )

    def _schedule(self, conversation_id: str, plan_id: str) -> None:
        plan = self.store.get_orchestration_plan(plan_id)
        if not plan or plan["status"] not in {
            OrchestrationPlanStatus.RUNNING.value,
            OrchestrationPlanStatus.APPROVED.value,
        }:
            return
        max_parallel = min(int((plan.get("plan") or {}).get("max_parallel", 4)), 4)
        assignments = [
            item for item in self.store.list_assignments(conversation_id) if item.get("plan_id") == plan_id
        ]
        running = sum(1 for item in assignments if item["status"] == AssignmentStatus.RUNNING.value)
        for assignment in sorted(assignments, key=lambda item: str(item["assignment_id"])):
            if running >= max_parallel:
                break
            if assignment["status"] != AssignmentStatus.QUEUED.value:
                continue
            dependencies = self.store.list_assignment_dependencies(str(assignment["assignment_id"]))
            if any(self._assignment(item)["status"] != AssignmentStatus.COMPLETED.value for item in dependencies):
                continue
            self._start_assignment(assignment)
            running += 1
        latest = [
            item for item in self.store.list_assignments(conversation_id) if item.get("plan_id") == plan_id
        ]
        if latest and all(item["status"] == AssignmentStatus.COMPLETED.value for item in latest):
            self.store.update_orchestration_plan(plan_id, status=OrchestrationPlanStatus.COMPLETED.value)
            self.store.append_conversation_event(
                conversation_id,
                "orchestration.completed",
                {"plan_id": plan_id},
                actor="supervisor",
            )

    def _ensure_reviewers_or_finalize(self, conversation_id: str) -> None:
        assignments = self.store.list_assignments(conversation_id)
        primary = [item for item in assignments if item["dispatch_kind"] in {"direct", "orchestrated"}]
        if not primary or any(item["status"] not in {"completed", "cancelled"} for item in primary):
            return
        contract = self._active_contract(conversation_id)
        profile = str(contract["profile"]).lower()
        required_tags = ["review"] + (["security-review"] if profile == "strict" else [])
        for tag in required_tags:
            existing = [item for item in assignments if item["dispatch_kind"] == tag]
            if existing:
                if any(item["status"] != AssignmentStatus.COMPLETED.value for item in existing):
                    return
                continue
            conversation = self._conversation(conversation_id)
            excluded = str(conversation.get("primary_agent_id") or "")
            reviewer = next(
                (
                    item for item in self.registry.agents.values()
                    if item.enabled and item.agent_id != excluded and tag in item.capability_tags
                ),
                None,
            )
            if not reviewer:
                self.store.update_conversation(conversation_id, status="needs_user")
                self.store.append_conversation_event(
                    conversation_id,
                    "delivery.blocked",
                    {"reason": f"no independent {tag} agent is configured"},
                    actor="supervisor",
                )
                return
            review = self._create_assignment(
                conversation_id=conversation_id,
                agent_id=reviewer.agent_id,
                dispatch_kind=tag,
                work_mode="consult",
                status=AssignmentStatus.QUEUED.value,
                title=f"Independent {tag}",
                brief="Review the integrated worktree against every frozen delivery standard item.",
                allowed_scope=list(contract["allowed_scope"]),
                deliverables=[f"独立 {tag} 结论"],
                completion=["逐项评估冻结的交付标准并报告阻断问题"],
                proof=["带 Agent 身份和 Assignment 绑定的 review manifest"],
            )
            self._start_assignment(review)
            return
        self.store.update_conversation(conversation_id, status="verifying")
        self.store.append_conversation_event(
            conversation_id,
            "delivery.verification_requested",
            {"contract_id": contract["contract_id"], "assignment_count": len(assignments)},
            actor="supervisor",
        )
        self._finalize_delivery(conversation_id)

    def _finalize_delivery(self, conversation_id: str) -> dict[str, Any]:
        from .collaboration_delivery import finalize_delivery

        return finalize_delivery(self, conversation_id)

    def _human_verification_approved(self, conversation_id: str) -> bool:
        events = self.store.conversation_events(conversation_id)
        if any(item["type"] == "verification.approved" for item in events):
            return True
        # An explicit approval of the frozen DAG also satisfies the Strict plan checkpoint.
        return any(item["type"] == "orchestration.plan_approved" for item in events)

    def _topological_assignments(self, conversation_id: str, plan_id: str) -> list[dict[str, Any]]:
        assignments = {
            str(item["assignment_id"]): item
            for item in self.store.list_assignments(conversation_id)
            if item.get("plan_id") == plan_id
        }
        remaining = set(assignments)
        completed: set[str] = set()
        result: list[dict[str, Any]] = []
        while remaining:
            ready = sorted(
                item for item in remaining
                if set(self.store.list_assignment_dependencies(item)) <= completed
            )
            if not ready:
                raise RuntimeError("assignment dependency graph contains a cycle")
            for assignment_id in ready:
                result.append(assignments[assignment_id])
                completed.add(assignment_id)
                remaining.remove(assignment_id)
        return result

    def _assignments_with_dependencies(self, conversation_id: str) -> list[dict[str, Any]]:
        return [
            {**item, "dependencies": self.store.list_assignment_dependencies(str(item["assignment_id"]))}
            for item in self.store.list_assignments(conversation_id)
        ]

    def _participants(self, conversation_id: str) -> list[dict[str, object]]:
        agent_ids = {
            str(item["agent_id"]) for item in self.store.list_assignments(conversation_id)
        }
        available = {str(item["agent_id"]): item for item in self.registry.list()}
        return [available[item] for item in sorted(agent_ids) if item in available]

    def _session_for_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT * FROM agent_sessions WHERE assignment_id = ? ORDER BY created_at DESC LIMIT 1",
            (assignment_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        if isinstance(result.get("metadata"), str):
            result["metadata"] = json.loads(result["metadata"])
        return result

    def _active_contract(self, conversation_id: str) -> dict[str, Any]:
        contracts = self.store.delivery_contracts(conversation_id)
        contract = next((item for item in reversed(contracts) if item["status"] == "active"), None)
        if not contract:
            raise RuntimeError("conversation has no active delivery contract")
        return contract

    def _active_plan(self, conversation_id: str) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        plan = self.store.get_orchestration_plan(str(conversation.get("active_plan_id") or ""))
        if not plan:
            raise RuntimeError("conversation has no active orchestration plan")
        return plan

    def _conversation(self, conversation_id: str) -> dict[str, Any]:
        conversation = self.store.get_conversation(conversation_id)
        if not conversation:
            raise FileNotFoundError(conversation_id)
        return conversation

    def _assignment(self, assignment_id: str) -> dict[str, Any]:
        assignment = self.store.get_assignment(assignment_id)
        if not assignment:
            raise FileNotFoundError(assignment_id)
        return assignment

    @staticmethod
    def _validate_changeset_scope(change_set: ChangeSet, allowed_scope: list[str]) -> None:
        if "workspace" in allowed_scope or "." in allowed_scope:
            return
        normalized = [item.strip("/\\") for item in allowed_scope]
        violations = [
            operation.path for operation in change_set.operations
            if not any(operation.path == scope or operation.path.startswith(scope + "/") for scope in normalized)
        ]
        if violations:
            raise PermissionError(f"assignment ChangeSet exceeds allowed scope: {violations}")

    @staticmethod
    def _scope_is_subset(requested: list[str], parent: list[str]) -> bool:
        if "workspace" in parent or "." in parent:
            return True
        parent_normalized = [item.strip("/\\") for item in parent]
        for item in requested:
            value = item.strip("/\\")
            if not any(value == root or value.startswith(root + "/") for root in parent_normalized):
                return False
        return True

    @staticmethod
    def _plan_expands_boundary(old: OrchestrationPlanV1, new: OrchestrationPlanV1) -> bool:
        if new.max_parallel > old.max_parallel:
            return True
        old_nodes = {item.id: item for item in old.nodes}
        for node in new.nodes:
            previous = old_nodes.get(node.id)
            if previous is None:
                continue
            if node.agent_id != previous.agent_id or node.work_mode != previous.work_mode:
                return True
        return False

    @staticmethod
    def _now() -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat()


__all__ = ["CollaborationService"]

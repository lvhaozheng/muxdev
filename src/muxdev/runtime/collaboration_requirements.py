"""Requirement discovery and Conversation-native interaction lifecycle."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..models import AssignmentStatus, ConversationMode
from ..services.agents import AgentUnavailableError
from .agent_sessions import SessionLifecycleError
from .change_tracking import change_monitors
from .delivery_standards import compile_deliverables


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


class CollaborationRequirementsMixin:
    def _settle_assignment_start_failure(
        self,
        assignment: Mapping[str, Any],
        error: Exception,
        assignment_id: str,
        conversation_id: str,
        run_id: str,
        agent_id: str,
        lane_key: str,
        attempt: int,
    ) -> dict[str, Any]:
        change_monitors.stop(run_id)
        error_detail = (
            error.detail()
            if isinstance(error, (AgentUnavailableError, SessionLifecycleError))
            else {
                "code": "session_launch_failed",
                "message": str(error),
                "remediation": "检查 Agent Doctor 和终端能力后重新启动 Session。",
                "retryable": True,
            }
        )
        failed = self.store.update_assignment(
            assignment_id,
            status=AssignmentStatus.FAILED.value,
            metadata={"failure": str(error)},
        )
        self.store.update_run(run_id, status="failed", current_stage=None)
        self.store.upsert_stage(
            run_id,
            "assignment",
            role=str(assignment["dispatch_kind"]),
            provider=agent_id,
            status="failed",
            attempt=attempt,
            result={"assignment_id": assignment_id, "failure": error_detail},
        )
        self.store.update_conversation(
            conversation_id,
            active_run_id=run_id,
            status="needs_user",
            metadata={"startup_error": error_detail},
        )
        session = self.store.find_agent_session(conversation_id, agent_id, lane_key)
        session_id = str((session or {}).get("session_id") or "") or None
        self.store.append_conversation_event(
            conversation_id,
            "run.failed_to_start",
            {
                "assignment_id": assignment_id,
                "run_id": run_id,
                "session_id": session_id,
                "reason": str(error),
                "error": error_detail,
            },
            actor="runtime",
            run_id=run_id,
            assignment_id=assignment_id,
            session_id=session_id,
        )
        return failed

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
        deliverables: Sequence[Mapping[str, object]] | None = None,
        auto_start_when_ready: bool = True,
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
        primary = self.registry.require_available(str(primary_id))
        orchestrator = None
        if parsed_mode == ConversationMode.ORCHESTRATED:
            orchestrator = self.registry.require_available(
                orchestrator_agent_id or primary.agent_id
            )
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
        compiled_standard = (
            compile_deliverables("change", profile, list(deliverables or []))
            if deliverables
            else None
        )
        if delivery_standard is not None and compiled_standard is not None:
            raise ValueError("use deliverables or delivery_standard, not both")
        if delivery_standard is not None or compiled_standard is not None:
            standard_input = (
                dict(delivery_standard)
                if delivery_standard is not None
                else {"custom_items": list((compiled_standard or {}).get("custom_items") or [])}
            )
            detail = self.conversations.revise_contract(
                conversation_id,
                {"delivery_standard": standard_input},
            )
        self.store.update_conversation(
            conversation_id,
            mode=parsed_mode.value,
            primary_agent_id=primary.agent_id,
            orchestrator_agent_id=orchestrator.agent_id if orchestrator else None,
            status="clarifying",
            metadata={
                "max_parallel": max_parallel,
                "cli_native": True,
                "auto_start_when_ready": bool(auto_start_when_ready),
                "deliverables": [dict(item) for item in (deliverables or [])],
                "requirements": {"status": "assessing"},
            },
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
        assessment = self._assess_requirements(
            goal,
            acceptance_criteria=list(acceptance_criteria or []),
            deliverables=list(deliverables or []),
        )
        if assessment["status"] == "needs_input":
            self.clarify(conversation_id, assessment)
        else:
            self.ready(
                conversation_id,
                assessment,
                auto_execute=bool(auto_start_when_ready),
            )
        return self.get(conversation_id)

    def get(self, conversation_id: str) -> dict[str, Any]:
        detail = self.conversations.get(conversation_id)
        conversation = detail["conversation"]
        sessions = self.store.list_agent_sessions(conversation_id)
        for session in sessions:
            session["generations"] = self.store.list_session_generations(str(session["session_id"]))
        all_runs = self.store.list_conversation_runs(conversation_id)
        metadata = conversation.get("metadata") if isinstance(conversation.get("metadata"), dict) else {}
        native = conversation.get("mode") != ConversationMode.LEGACY_PIPELINE.value
        interactions = (
            self.store.list_conversation_interactions(conversation_id)
            if native else list(detail.get("interactions") or [])
        )
        detail.update(
            {
                "participants": self._participants(conversation_id),
                "plans": self.store.list_orchestration_plans(conversation_id),
                "assignments": self._assignments_with_dependencies(conversation_id),
                "sessions": sessions,
                "timeline": self.store.conversation_events(conversation_id),
                "mode": conversation.get("mode", ConversationMode.LEGACY_PIPELINE.value),
                "requirements": metadata.get("requirements") or {"status": "unknown"},
                "interactions": interactions,
                "runs": all_runs if native else list(detail.get("runs") or []),
                "delivery_summary": self._delivery_summary(detail, interactions),
            }
        )
        return detail

    def snapshot_context(self, conversation_id: str) -> dict[str, Any]:
        """Return only the bounded projections required by Conversation Snapshot.

        The detailed Conversation endpoint intentionally includes the complete
        event history and verifies its chain. Snapshot owns a separate, bounded
        activity query, so routing it through ``get`` would read and hash the
        entire ledger before discarding it.
        """
        conversation = self._conversation(conversation_id)
        native = conversation.get("mode") != ConversationMode.LEGACY_PIPELINE.value
        return {
            "conversation": conversation,
            "participants": self._participants(conversation_id),
            "assignments": self._assignments_with_dependencies(conversation_id),
            "orchestration_plans": self.store.list_orchestration_plans(
                conversation_id
            ),
            "sessions": self.store.list_agent_sessions(conversation_id),
            "interactions": (
                self.store.list_conversation_interactions(conversation_id)
                if native
                else []
            ),
        }

    def add_message(
        self,
        conversation_id: str,
        content: str,
        *,
        recipients: Sequence[str] | None = None,
        dispatch_kind: str | None = None,
        interaction_id: str | None = None,
    ) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        content = content.strip()
        if not content:
            raise ValueError("message content is required")
        requested = list(dict.fromkeys([*(recipients or []), *_MENTION.findall(content)]))
        if not requested:
            requested = [str(conversation.get("primary_agent_id") or "")]
        requested = [item for item in requested if item]
        kind = dispatch_kind or "message"
        if (
            not interaction_id
            and kind == "message"
            and str(conversation.get("status") or "") == "idle"
        ):
            return self._start_next_turn(conversation_id, content, requested[0])

        if interaction_id:
            return self._respond_to_interaction(
                conversation,
                interaction_id=interaction_id,
                content=content,
                recipients=requested,
            )

        prepared_routes = (
            self._prepare_message_routes(conversation_id, requested)
            if kind == "message"
            else []
        )
        if kind != "message":
            for recipient in requested:
                self.registry.require_available(recipient)

        event_id = self.store.append_conversation_event(
            conversation_id,
            "user.message",
            {
                "content": content,
                "recipients": requested,
                "dispatch_kind": kind,
            },
            actor="developer",
        )
        if kind == "message":
            routed = self._send_prepared_message(prepared_routes, content)
        else:
            routed = self._dispatch_explicit_routes(
                conversation_id,
                content,
                requested,
                kind,
            )
        return {"event_id": event_id, "recipients": routed, "interaction_id": interaction_id}

    def _start_next_turn(
        self,
        conversation_id: str,
        content: str,
        agent_id: str,
    ) -> dict[str, Any]:
        self.registry.require_available(agent_id)
        event_id = self.store.append_conversation_event(
            conversation_id,
            "user.message",
            {
                "content": content,
                "recipients": [agent_id],
                "dispatch_kind": "direct",
                "new_turn": True,
            },
            actor="developer",
        )
        assignment = self.dispatch(
            conversation_id,
            {
                "agent_id": agent_id,
                "dispatch_kind": "direct",
                "work_mode": "write",
                "title": content[:120],
                "brief": content,
                "deliverables": ["本轮用户请求的可验证交付"],
                "completion": ["完成本轮请求并满足当前交付契约"],
                "proof": ["内容寻址 ChangeSet 与 Runtime 验证"],
            },
        )
        return {
            "event_id": event_id,
            "recipients": [{
                "agent_id": agent_id,
                "assignment_id": str(assignment["assignment_id"]),
                "run_id": str(assignment.get("run_id") or ""),
            }],
            "interaction_id": None,
            "new_turn": True,
        }

    def _prepare_message_routes(
        self,
        conversation_id: str,
        recipients: Sequence[str],
    ) -> list[tuple[str, dict[str, Any] | None, dict[str, Any]]]:
        routes: list[tuple[str, dict[str, Any] | None, dict[str, Any]]] = []
        assignments = self.store.list_assignments(conversation_id)
        for recipient in recipients:
            self.registry.require_available(recipient)
            active = next(
                (
                    item
                    for item in reversed(assignments)
                    if item["agent_id"] == recipient
                    and item["status"] in _ACTIVE_ASSIGNMENT_STATES
                ),
                None,
            )
            session = (
                self._session_for_assignment(str(active["assignment_id"]))
                if active
                else None
            )
            if session:
                self.sessions.ensure_live(str(session["session_id"]))
            else:
                session = self._ensure_main_session(conversation_id, recipient)
            routes.append((recipient, active, session))
        return routes

    def _send_prepared_message(
        self,
        routes: Sequence[tuple[str, dict[str, Any] | None, dict[str, Any]]],
        content: str,
    ) -> list[dict[str, str]]:
        routed: list[dict[str, str]] = []
        for recipient, active, session in routes:
            self.sessions.send_runtime(str(session["session_id"]), content)
            route = {
                "agent_id": recipient,
                "session_id": str(session["session_id"]),
            }
            if active:
                route["assignment_id"] = str(active["assignment_id"])
            routed.append(route)
        return routed

    def _dispatch_explicit_routes(
        self,
        conversation_id: str,
        content: str,
        recipients: Sequence[str],
        kind: str,
    ) -> list[dict[str, str]]:
        routed: list[dict[str, str]] = []
        for recipient in recipients:
            assignment = self.dispatch(
                conversation_id,
                {
                    "agent_id": recipient,
                    "dispatch_kind": kind,
                    "work_mode": "write" if kind in {"write", "direct"} else "consult",
                    "title": f"定向协作：{content[:60]}",
                    "brief": content,
                    "deliverables": ["面向发起者的结构化回报"],
                    "completion": ["回答定向问题或完成声明范围"],
                    "proof": ["Agent report manifest"],
                },
            )
            routed.append({
                "agent_id": recipient,
                "assignment_id": str(assignment["assignment_id"]),
                "run_id": str(assignment.get("run_id") or ""),
            })
        return routed

    def _respond_to_interaction(
        self,
        conversation: Mapping[str, Any],
        *,
        interaction_id: str,
        content: str,
        recipients: Sequence[str],
    ) -> dict[str, Any]:
        conversation_id = str(conversation["conversation_id"])
        interaction = self.store.get_conversation_interaction(interaction_id)
        if (
            not interaction
            or str(interaction["conversation_id"]) != conversation_id
        ):
            raise FileNotFoundError(interaction_id)
        assignment_id = str(interaction.get("assignment_id") or "")
        run_id = str(interaction.get("run_id") or "")
        session: dict[str, Any] | None = None
        if assignment_id:
            session = self._session_for_assignment(assignment_id)
            if not session:
                session = self._ensure_main_session(
                    conversation_id,
                    str(conversation.get("primary_agent_id") or ""),
                )
            self.sessions.ensure_live(str(session["session_id"]))

        event_id = self.store.append_conversation_event(
            conversation_id,
            "user.message",
            {
                "content": content,
                "recipients": list(recipients),
                "dispatch_kind": "message",
                "interaction_id": interaction_id,
            },
            actor="developer",
        )
        self.store.respond_conversation_interaction(interaction_id, content)
        self.store.append_conversation_event(
            conversation_id,
            "interaction.responded",
            {"interaction_id": interaction_id, "response": content},
            actor="developer",
            run_id=run_id or None,
            assignment_id=assignment_id or None,
        )
        routed: list[dict[str, str]] = []
        if run_id and assignment_id and session:
            self.store.update_run(
                run_id,
                status="running",
                current_stage="assignment",
            )
            self.store.update_assignment(
                assignment_id,
                status=AssignmentStatus.RUNNING.value,
            )
            self.sessions.send_runtime(str(session["session_id"]), content)
            routed.append(
                {
                    "agent_id": str(session["agent_id"]),
                    "session_id": str(session["session_id"]),
                    "assignment_id": assignment_id,
                }
            )
        elif not self.store.list_conversation_interactions(
            conversation_id,
            pending_only=True,
        ):
            requirements = dict(
                (conversation.get("metadata") or {}).get("requirements") or {}
            )
            requirements["clarification_response"] = content
            requirements["status"] = "ready"
            requirements.setdefault(
                "acceptance_criteria",
                list(
                    self._active_contract(conversation_id)[
                        "acceptance_criteria"
                    ]
                ),
            )
            requirements.setdefault(
                "deliverables",
                list((conversation.get("metadata") or {}).get("deliverables") or []),
            )
            self.ready(
                conversation_id,
                requirements,
                auto_execute=bool(
                    (conversation.get("metadata") or {}).get(
                        "auto_start_when_ready",
                        True,
                    )
                ),
            )
        return {
            "event_id": event_id,
            "recipients": routed,
            "interaction_id": interaction_id,
        }

    def request_assignment_input(
        self, assignment_id: str, assessment: Mapping[str, object]
    ) -> dict[str, Any]:
        assignment = self._assignment(assignment_id)
        if assignment["status"] != AssignmentStatus.RUNNING.value:
            raise ValueError("only a running Assignment can request user input")
        questions = assessment.get("questions")
        if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
            raise ValueError("input request requires 1-3 questions")
        run_id = str(assignment.get("run_id") or "")
        if not run_id:
            raise RuntimeError("Assignment Run is missing")
        created = []
        for index, question in enumerate(questions):
            if not isinstance(question, Mapping):
                raise ValueError("questions must be objects")
            created.append(self.store.create_conversation_interaction(
                conversation_id=str(assignment["conversation_id"]),
                run_id=run_id,
                assignment_id=assignment_id,
                kind="run_input",
                requirement_id=str(question.get("id") or f"run.input.{index + 1}"),
                prompt=str(question.get("question") or "").strip(),
                options=(question.get("options") if isinstance(question.get("options"), list) else []),
            ))
        self.store.update_assignment(assignment_id, status=AssignmentStatus.WAITING_USER.value)
        self.store.update_run(run_id, status="waiting_user", current_stage="assignment")
        self.store.append_conversation_event(
            str(assignment["conversation_id"]),
            "interaction.requested",
            {"assignment_id": assignment_id, "interaction_ids": [item["interaction_id"] for item in created]},
            actor=str(assignment["agent_id"]),
            run_id=run_id,
        )
        return {"run_id": run_id, "assignment_id": assignment_id, "interactions": created}

    def clarify(self, conversation_id: str, assessment: Mapping[str, object]) -> dict[str, Any]:
        questions = assessment.get("questions")
        if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
            raise ValueError("clarification assessment requires 1-3 questions")
        if self.store.list_assignments(conversation_id):
            raise RuntimeError("pre-execution clarification cannot be added after an Assignment starts")
        self.store.update_conversation(
            conversation_id,
            status="clarifying",
            metadata={"requirements": {**dict(assessment), "status": "needs_input"}},
        )
        created: list[dict[str, Any]] = []
        for index, question in enumerate(questions):
            if not isinstance(question, Mapping):
                raise ValueError("clarification questions must be objects")
            created.append(self.store.create_conversation_interaction(
                conversation_id=conversation_id,
                kind="clarification",
                requirement_id=str(question.get("id") or f"requirement.{index + 1}"),
                prompt=str(question.get("question") or "").strip(),
                options=(question.get("options") if isinstance(question.get("options"), list) else []),
            ))
        self.store.append_conversation_event(
            conversation_id,
            "requirements.needs_input",
            {"interaction_ids": [item["interaction_id"] for item in created]},
            actor=str(self._conversation(conversation_id).get("primary_agent_id") or "supervisor"),
        )
        return {"status": "needs_input", "interactions": created}

    def ready(
        self,
        conversation_id: str,
        requirements: Mapping[str, object],
        *,
        auto_execute: bool | None = None,
    ) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        frozen = {
            **dict(requirements),
            "status": "ready",
            "goal": str(requirements.get("goal") or conversation["goal"]),
            "acceptance_criteria": list(
                requirements.get("acceptance_criteria")
                or self._active_contract(conversation_id)["acceptance_criteria"]
            ),
            "deliverables": list(
                requirements.get("deliverables")
                or (conversation.get("metadata") or {}).get("deliverables")
                or [{"type": "code_change"}]
            ),
        }
        self.store.update_conversation(
            conversation_id,
            status="clarifying",
            metadata={"requirements": frozen},
        )
        self.store.append_conversation_event(
            conversation_id,
            "requirements.ready",
            {"requirements": frozen},
            actor=str(conversation.get("primary_agent_id") or "supervisor"),
        )
        should_start = (
            bool((conversation.get("metadata") or {}).get("auto_start_when_ready", True))
            if auto_execute is None else bool(auto_execute)
        )
        return self.execute(conversation_id) if should_start else self.get(conversation_id)

    def execute(self, conversation_id: str) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        requirements = dict((conversation.get("metadata") or {}).get("requirements") or {})
        if requirements.get("status") != "ready":
            raise RuntimeError("requirements are not ready; answer the clarification first")
        existing = [
            item for item in self.store.list_assignments(conversation_id)
            if item["dispatch_kind"] in {"direct", "orchestrate"}
            and item["status"] != AssignmentStatus.CANCELLED.value
        ]
        if existing:
            return self.get(conversation_id)
        contract = self._active_contract(conversation_id)
        orchestrated = conversation.get("mode") == ConversationMode.ORCHESTRATED.value
        assignment = self._create_assignment(
            conversation_id=conversation_id,
            agent_id=str(
                conversation.get("orchestrator_agent_id") if orchestrated
                else conversation.get("primary_agent_id")
            ),
            dispatch_kind="orchestrate" if orchestrated else "direct",
            work_mode="consult" if orchestrated else "write",
            status=AssignmentStatus.QUEUED.value,
            title=("提出多 Agent 编排计划" if orchestrated else str(conversation["title"])),
            brief=str(contract["goal"]),
            allowed_scope=list(contract["allowed_scope"]),
            deliverables=(
                ["可确认的 OrchestrationPlanV1"] if orchestrated
                else [self._deliverable_label(item) for item in requirements["deliverables"]]
            ),
            completion=(
                ["计划无环、覆盖任务契约并显式分配 Agent"] if orchestrated
                else list(frozen for frozen in requirements["acceptance_criteria"])
            ),
            proof=(
                ["Runtime 校验通过的计划版本"] if orchestrated
                else ["内容寻址 ChangeSet", "Runtime 确定性检查", "独立交付评审"]
            ),
        )
        self.store.update_conversation(conversation_id, status="working")
        self._start_assignment(assignment)
        return self.get(conversation_id)

    def _ensure_main_session(
        self,
        conversation_id: str,
        agent_id: str,
        *,
        bootstrap: str = "",
    ) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        worktree = Path(str((conversation.get("metadata") or {}).get("worktree") or ""))
        existing = self.store.find_agent_session(conversation_id, agent_id, "main")
        if existing:
            session_id = str(existing["session_id"])
            existing = self.sessions.ensure_live(session_id)
            if bootstrap:
                self.sessions.send_runtime(session_id, bootstrap)
            return self.store.get_agent_session(str(existing["session_id"])) or existing
        return self.sessions.start(
            conversation_id=conversation_id,
            assignment_id=None,
            agent_id=agent_id,
            worktree=worktree,
            bootstrap=bootstrap or self._conversation_join_bootstrap(conversation_id),
            lane_key="main",
            lane_type="main",
        )

    def _conversation_join_bootstrap(self, conversation_id: str) -> str:
        contract = self._active_contract(conversation_id)
        return json.dumps(
            {
                "schema": "muxdev.conversation-context.v1",
                "goal": contract["goal"],
                "acceptance_criteria": contract["acceptance_criteria"],
                "allowed_scope": contract["allowed_scope"],
                "instruction": "You joined this Conversation. Messages alone are not Assignments or Runs.",
            },
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def _assess_requirements(
        goal: str,
        *,
        acceptance_criteria: Sequence[str],
        deliverables: Sequence[Mapping[str, object]],
    ) -> dict[str, Any]:
        normalized = " ".join(goal.lower().split())
        vague_phrases = {
            "帮我优化一下", "帮我改一下", "做一个东西", "处理一下", "完善一下",
            "改进一下", "make it better", "build something", "improve this", "fix it",
        }
        explicit_signal = bool(
            re.search(r"(?:[/\\]|\.[a-z0-9]{1,8}\b|api\b|接口|文件|报告|测试|实现|创建|新增|删除|修改|inspect|create|add|remove|fix|test)", normalized)
        )
        needs_input = (
            not acceptance_criteria
            and not deliverables
            and (len(normalized) < 10 or normalized in vague_phrases or not explicit_signal)
        )
        if needs_input:
            return {
                "status": "needs_input",
                "goal": goal,
                "questions": [{
                    "id": "deliverable.type",
                    "question": "这次任务完成后，你希望拿到什么？",
                    "options": [
                        {"label": "代码修改", "description": "修改当前项目并提供可验证 ChangeSet。"},
                        {"label": "指定文件或报告", "description": "生成有明确路径或格式的内容产物。"},
                        {"label": "分析回答", "description": "交付结构化结论，不修改项目。"},
                    ],
                }],
            }
        return {
            "status": "ready",
            "goal": goal,
            "acceptance_criteria": list(acceptance_criteria or ["完成用户描述的任务并通过所选可信交付门禁。"]),
            "deliverables": [dict(item) for item in deliverables] or [{"type": "code_change"}],
        }

    @staticmethod
    def _deliverable_label(value: object) -> str:
        if not isinstance(value, Mapping):
            return str(value)
        return str(
            value.get("path")
            or value.get("name")
            or value.get("description")
            or value.get("type")
            or "交付物"
        )

    @staticmethod
    def _delivery_summary(
        detail: Mapping[str, Any], interactions: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        standard = detail.get("delivery_standard")
        stages = standard.get("stages", []) if isinstance(standard, Mapping) else []
        missing = [
            str(item.get("label") or item.get("title") or item.get("id") or "未达标项")
            for item in stages
            if isinstance(item, Mapping) and str(item.get("status") or "").lower() in {"blocked", "failed"}
        ]
        pending = [item for item in interactions if item.get("status") == "pending"]
        return {
            "status": "needs_input" if pending else ("blocked" if missing else "pending"),
            "deliver": (detail.get("conversation") or {}).get("goal"),
            "missing": missing,
            "pending_questions": len(pending),
        }

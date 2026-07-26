"""Fair project/global capacity scheduling for collaboration Assignments."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..models import AssignmentStatus, OrchestrationPlanStatus
from .agent_sessions import SessionCapacityError, session_capacity_available
from .change_tracking import (
    ChangeTrackingContext,
    ChangeTrackingService,
    change_monitors,
)
from .workspace import snapshot_workspace


class CollaborationSchedulingMixin:
    workspace: Any
    store: Any

    def _start_assignment(
        self,
        assignment: Mapping[str, Any],
        *,
        recovery: bool = False,
    ) -> dict[str, Any]:
        assignment_id = str(assignment["assignment_id"])
        conversation_id = str(assignment["conversation_id"])
        conversation = self._conversation(conversation_id)
        integration = Path(
            str((conversation.get("metadata") or {}).get("worktree") or "")
        )
        agent_id = str(assignment["agent_id"])
        # Worktrees remain isolated per Assignment, but the interactive CLI is
        # a Conversation-scoped logical Session. A new Assignment may advance
        # its generation; it must not create another terminal for the same Agent.
        lane_key = "main"
        lane_type = "main"
        capacity_queued = self._queue_when_at_capacity(
            assignment,
            conversation_id=conversation_id,
            assignment_id=assignment_id,
            agent_id=agent_id,
        )
        if capacity_queued:
            return capacity_queued
        worktree, strategy, _use_main_lane = self._assignment_worktree(
            assignment, integration
        )
        baseline = snapshot_workspace(worktree)
        updated = self.store.update_assignment(
            assignment_id,
            status=AssignmentStatus.RUNNING.value,
            baseline_digest=baseline.digest,
            worktree=str(worktree),
            metadata={
                "baseline_manifest": baseline.to_dict(),
                "worktree_strategy": strategy,
                "failure": None,
                "blocked_reason": None,
                "waiting_reason": None,
            },
        )
        bootstrap = self._bootstrap(updated)
        contract = self._active_contract(conversation_id)
        updated, run_id, attempt = self._ensure_assignment_run(
            updated, contract=contract, agent_id=agent_id
        )
        tracking_context = ChangeTrackingContext(
            conversation_id=conversation_id,
            run_id=run_id,
            assignment_id=assignment_id,
            worktree=worktree,
            author=agent_id,
        )
        if updated["work_mode"] == "write":
            if not self.store.file_baselines(run_id):
                ChangeTrackingService(
                    self.workspace, self.store
                ).capture_baseline(tracking_context)
            change_monitors.start(self.workspace, tracking_context)
        self._mark_assignment_running(
            updated,
            conversation_id=conversation_id,
            run_id=run_id,
            agent_id=agent_id,
            attempt=attempt,
        )
        try:
            session = self._activate_assignment_session(
                updated,
                worktree=worktree,
                bootstrap=bootstrap,
                lane_key=lane_key,
                lane_type=lane_type,
                recovery=recovery,
            )
        except SessionCapacityError:
            change_monitors.stop(run_id)
            self.store.update_run(run_id, status="created", current_stage=None)
            return self.store.update_assignment(
                assignment_id,
                status=AssignmentStatus.QUEUED.value,
                metadata={"capacity_queued": True},
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return self._settle_assignment_start_failure(
                updated,
                exc,
                assignment_id,
                conversation_id,
                run_id,
                agent_id,
                lane_key,
                attempt,
            )
        except Exception:
            change_monitors.stop(run_id)
            raise
        self.store.bind_run_session(run_id, str(session["session_id"]))
        if self._assignment_or_session_failed(assignment_id, session):
            return self.store.get_assignment(assignment_id) or updated
        self.store.append_conversation_event(
            conversation_id,
            "assignment.started",
            {
                "assignment_id": assignment_id,
                "agent_id": updated["agent_id"],
                "session_id": session["session_id"],
                "run_id": run_id,
                "lane_key": lane_key,
                "worktree_strategy": strategy,
            },
            actor="supervisor",
            run_id=run_id,
        )
        return updated

    def _mark_assignment_running(
        self,
        assignment: Mapping[str, Any],
        *,
        conversation_id: str,
        run_id: str,
        agent_id: str,
        attempt: int,
    ) -> None:
        self.store.update_conversation(
            conversation_id, active_run_id=run_id, status="working"
        )
        self.store.update_run(run_id, status="running", current_stage="assignment")
        self.store.upsert_stage(
            run_id,
            "assignment",
            role=str(assignment["dispatch_kind"]),
            provider=agent_id,
            status="running",
            attempt=attempt,
            result={"assignment_id": assignment["assignment_id"]},
        )

    def _assignment_or_session_failed(
        self, assignment_id: str, session: Mapping[str, Any]
    ) -> bool:
        current_session = self.store.get_agent_session(str(session["session_id"]))
        current_assignment = self.store.get_assignment(assignment_id)
        return (
            str((current_session or {}).get("status") or "") == "failed"
            or str((current_assignment or {}).get("status") or "") == "failed"
        )

    def _queue_when_at_capacity(
        self,
        assignment: Mapping[str, Any],
        *,
        conversation_id: str,
        assignment_id: str,
        agent_id: str,
    ) -> dict[str, Any] | None:
        existing = self.store.find_conversation_agent_session(
            conversation_id, agent_id
        )
        current_assignment_id = str(
            (existing or {}).get("current_assignment_id") or ""
        )
        current_assignment = (
            self.store.get_assignment(current_assignment_id)
            if current_assignment_id
            else None
        )
        if (
            current_assignment
            and current_assignment_id != assignment_id
            and str(current_assignment.get("status") or "")
            in {
                AssignmentStatus.RUNNING.value,
                AssignmentStatus.WAITING_USER.value,
                AssignmentStatus.REPORTED.value,
                AssignmentStatus.VERIFYING.value,
                AssignmentStatus.READY_TO_MERGE.value,
                AssignmentStatus.MERGING.value,
            }
        ):
            metadata = (
                assignment.get("metadata")
                if isinstance(assignment.get("metadata"), dict)
                else {}
            )
            queued = self.store.update_assignment(
                assignment_id,
                status=AssignmentStatus.QUEUED.value,
                metadata={"agent_session_queued": True},
            )
            if not metadata.get("agent_session_queued"):
                self.store.append_conversation_event(
                    conversation_id,
                    "assignment.agent_session_queued",
                    {
                        "assignment_id": assignment_id,
                        "agent_id": agent_id,
                        "active_assignment_id": current_assignment_id,
                        "session_id": existing["session_id"],
                    },
                    actor="runtime",
                    assignment_id=assignment_id,
                    session_id=str(existing["session_id"]),
                )
            return queued
        attached = bool(
            existing
            and self.sessions.snapshot(str(existing["session_id"])).get("attached")
        )
        if attached or session_capacity_available(self.workspace):
            return None
        metadata = (
            assignment.get("metadata")
            if isinstance(assignment.get("metadata"), dict)
            else {}
        )
        queued = self.store.update_assignment(
            assignment_id,
            status=AssignmentStatus.QUEUED.value,
            metadata={"capacity_queued": True},
        )
        if not metadata.get("capacity_queued"):
            self.store.append_conversation_event(
                conversation_id,
                "assignment.capacity_queued",
                {
                    "assignment_id": assignment_id,
                    "agent_id": agent_id,
                    "global_limit": 8,
                    "project_limit": 4,
                },
                actor="runtime",
                assignment_id=assignment_id,
            )
        return queued

    def schedule_queued(self) -> int:
        """Start oldest dependency-ready work while daemon capacity permits."""
        started = 0
        rows = self.store.connection.execute(
            """SELECT assignment_id FROM assignments
               WHERE status = ? ORDER BY created_at, assignment_id""",
            (AssignmentStatus.QUEUED.value,),
        ).fetchall()
        for row in rows:
            if not session_capacity_available(self.workspace):
                break
            assignment = self._assignment(str(row[0]))
            dependencies = self.store.list_assignment_dependencies(
                str(assignment["assignment_id"])
            )
            if any(
                self._assignment(item)["status"] != AssignmentStatus.COMPLETED.value
                for item in dependencies
            ):
                continue
            if not self._within_plan_capacity(assignment):
                continue
            result = self._start_assignment(assignment)
            if result.get("status") == AssignmentStatus.RUNNING.value:
                started += 1
        return started

    def _within_plan_capacity(self, assignment: dict[str, Any]) -> bool:
        plan_id = str(assignment.get("plan_id") or "")
        if not plan_id:
            return True
        plan = self.store.get_orchestration_plan(plan_id)
        if not plan or plan["status"] not in {
            OrchestrationPlanStatus.RUNNING.value,
            OrchestrationPlanStatus.APPROVED.value,
        }:
            return False
        assignments = [
            item
            for item in self.store.list_assignments(
                str(assignment["conversation_id"])
            )
            if item.get("plan_id") == plan_id
        ]
        max_parallel = min(
            int((plan.get("plan") or {}).get("max_parallel", 4)), 4
        )
        running = sum(
            1
            for item in assignments
            if item["status"] == AssignmentStatus.RUNNING.value
        )
        return running < max_parallel

    def _schedule(self, conversation_id: str, plan_id: str) -> None:
        plan = self.store.get_orchestration_plan(plan_id)
        if not plan or plan["status"] not in {
            OrchestrationPlanStatus.RUNNING.value,
            OrchestrationPlanStatus.APPROVED.value,
        }:
            return
        max_parallel = min(
            int((plan.get("plan") or {}).get("max_parallel", 4)), 4
        )
        assignments = [
            item
            for item in self.store.list_assignments(conversation_id)
            if item.get("plan_id") == plan_id
        ]
        running = sum(
            1
            for item in assignments
            if item["status"] == AssignmentStatus.RUNNING.value
        )
        running_agents = {
            str(item["agent_id"])
            for item in assignments
            if item["status"] == AssignmentStatus.RUNNING.value
        }
        for assignment in sorted(
            assignments, key=lambda item: str(item["assignment_id"])
        ):
            if running >= max_parallel:
                break
            if assignment["status"] != AssignmentStatus.QUEUED.value:
                continue
            dependencies = self.store.list_assignment_dependencies(
                str(assignment["assignment_id"])
            )
            if any(
                self._assignment(item)["status"] != AssignmentStatus.COMPLETED.value
                for item in dependencies
            ):
                continue
            if str(assignment["agent_id"]) in running_agents:
                continue
            result = self._start_assignment(assignment)
            if result.get("status") != AssignmentStatus.RUNNING.value:
                continue
            running += 1
            running_agents.add(str(assignment["agent_id"]))
        latest = [
            item
            for item in self.store.list_assignments(conversation_id)
            if item.get("plan_id") == plan_id
        ]
        if latest and all(
            item["status"] == AssignmentStatus.COMPLETED.value
            for item in latest
        ):
            self.store.update_orchestration_plan(
                plan_id, status=OrchestrationPlanStatus.COMPLETED.value
            )
            self.store.append_conversation_event(
                conversation_id,
                "orchestration.completed",
                {"plan_id": plan_id},
                actor="supervisor",
            )

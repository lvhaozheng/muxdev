"""Transport-only request models for the HTTP API.

Keeping these schemas outside the route module prevents validation concerns
from becoming coupled to task orchestration or HTML presentation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskCreateRequest(BaseModel):
    task: str
    workspace: str | None = None
    provider: str = "auto"
    workflow: str = "software-dev"
    gate: str | None = None
    require_approval: list[str] = Field(default_factory=list)
    max_cost_usd: float = 0.5
    role_providers: dict[str, str] = Field(default_factory=dict)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    ci_block_on_approval: bool = False
    depth: str | None = None
    automation: dict[str, Any] = Field(default_factory=dict)
    routing_policy: dict[str, Any] = Field(default_factory=dict)


class ContinueRequest(BaseModel):
    max_cost_usd: float = 0.5


class CancelRequest(BaseModel):
    reason: str = ""
    wait: bool = False
    timeout: float = 30.0


class ReconcileRequest(BaseModel):
    decision: str
    reason: str
    acknowledge_duplicate_risk: bool = False


class ApprovalFeedbackRequest(BaseModel):
    feedback: str
    max_cost_usd: float = 0.5


class ProviderActionResponseRequest(BaseModel):
    response: Any | None = None
    choice: str | None = None
    text: str | None = None
    max_cost_usd: float = 0.5

    def response_payload(self) -> Any:
        if self.response is not None:
            return self.response
        if self.choice is not None:
            return {"choice": self.choice}
        if self.text is not None:
            return {"text": self.text}
        return {"handled": True}


class MultiRepoPlanRequest(BaseModel):
    task: str
    repos: list[str] = Field(default_factory=list)
    mode: str = "design"
    workspace: str | None = None


class FeedbackRequest(BaseModel):
    kind: str
    source: str = "manual"
    content: str
    workspace: str | None = None
    run_id: str | None = None
    severity: str = "medium"
    provider: str = "mock"
    payload: dict[str, Any] = Field(default_factory=dict)
    auto_submit: bool = True


class StorageBackupRequest(BaseModel):
    scope: str = "all"


class ControlledAttestationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoutingReplayRequest(BaseModel):
    run_id: str
    benchmark_snapshot_id: str | None = None


class RoutingBenchmarkRequest(BaseModel):
    suite_id: str = "trusted-routing-v1"
    live: bool = False
    acknowledged: bool = False
    max_cost_usd: float | None = None


class BenchmarkReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suite_id: str = "trusted-routing-v1"


class DemoRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace: str | None = None

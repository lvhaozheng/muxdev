import type {
  AgentDefinition,
  ChangeSetView,
  Conversation,
  Project,
  ProjectRules,
  ProjectSkills,
  MessageDeliveryResponse,
  RemoteSkillSearch,
  RuleSource,
  ReviewView,
  ConversationSnapshot,
} from "./types";

export interface ApiErrorDetail {
  code: string;
  message: string;
  remediation?: string;
  retryable?: boolean;
  retry_after_ms?: number;
}

export class ApiError extends Error {
  code: string;
  remediation?: string;
  retryable: boolean;
  retryAfterMs?: number;
  status: number;

  constructor(status: number, detail: ApiErrorDetail) {
    super(detail.message);
    this.name = "ApiError";
    this.status = status;
    this.code = detail.code;
    this.remediation = detail.remediation;
    this.retryable = Boolean(detail.retryable);
    this.retryAfterMs = detail.retry_after_ms;
  }
}

function projectApi(projectId: string, path: string): string {
  return `/api/v2/projects/${encodeURIComponent(projectId)}${path}`;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail: ApiErrorDetail = {
      code: "http_error",
      message: `${response.status} ${response.statusText}`,
      retryable: response.status >= 500,
    };
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") {
        detail = { ...detail, message: body.detail };
      } else if (body.detail && typeof body.detail === "object") {
        const value = body.detail as Partial<ApiErrorDetail>;
        detail = {
          code: value.code || detail.code,
          message: value.message || detail.message,
          remediation: value.remediation,
          retryable: value.retryable ?? detail.retryable,
          retry_after_ms: value.retry_after_ms,
        };
      }
    } catch {
      // Keep the HTTP status when the body is not JSON.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export function listProjects(): Promise<Project[]> {
  return request<Project[]>("/api/v2/projects");
}

export function registerProject(path: string): Promise<Project> {
  return request<Project>("/api/v2/projects", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export async function listConversations(projectId: string): Promise<Conversation[]> {
  const result = await request<Array<Conversation | { conversation: Conversation }>>(
    projectApi(projectId, "/conversations?limit=200"),
  );
  return result.map((item) => ("conversation" in item ? item.conversation : item));
}

export function listAgents(projectId: string): Promise<AgentDefinition[]> {
  return request<AgentDefinition[]>(projectApi(projectId, "/agents"));
}

export function getConversationSnapshot(
  projectId: string,
  conversationId: string,
): Promise<ConversationSnapshot> {
  return request<ConversationSnapshot>(
    projectApi(
      projectId,
      `/conversations/${encodeURIComponent(conversationId)}/snapshot?limit=200`,
    ),
  );
}

export function createConversation(projectId: string, body: {
  goal: string;
  title?: string;
  mode: "direct" | "orchestrated";
  agent_id: string;
  deliverables: Array<Record<string, string>>;
  profile: "lite" | "standard" | "strict";
  auto_start_when_ready: boolean;
  rule_ids?: string[];
  collaborator_agent_ids?: string[];
}): Promise<{ conversation: Conversation }> {
  return request(projectApi(projectId, "/conversations"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function sendMessage(
  projectId: string,
  conversationId: string,
  content: string,
  interactionId?: string,
  recipients: string[] = [],
  dispatchKind = "message",
): Promise<MessageDeliveryResponse> {
  return request(
    projectApi(
      projectId,
      `/conversations/${encodeURIComponent(conversationId)}/messages`,
    ),
    {
    method: "POST",
    body: JSON.stringify({
      content,
      interaction_id: interactionId || null,
      recipients,
      dispatch_kind: dispatchKind,
    }),
    },
  );
}

export function getChanges(
  projectId: string,
  conversationId: string,
  runId?: string,
): Promise<ChangeSetView> {
  const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  return request(
    projectApi(
      projectId,
      `/conversations/${encodeURIComponent(conversationId)}/changes${query}`,
    ),
  );
}

export function getReview(
  projectId: string,
  conversationId: string,
  runId?: string,
): Promise<ReviewView> {
  const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  return request(
    projectApi(
      projectId,
      `/conversations/${encodeURIComponent(conversationId)}/review${query}`,
    ),
  );
}

export function getFile(
  projectId: string,
  conversationId: string,
  runId: string,
  path: string,
  view: "baseline" | "current" | "diff",
): Promise<{ content: string; binary: boolean; truncated: boolean }> {
  const query = new URLSearchParams({ run_id: runId, path, view });
  return request(
    projectApi(
      projectId,
      `/conversations/${encodeURIComponent(conversationId)}/file?${query.toString()}`,
    ),
  );
}

export function requestChanges(
  projectId: string,
  conversationId: string,
  message: string,
  references: Array<{ path: string; line?: number; end_line?: number }> = [],
): Promise<Record<string, unknown>> {
  return request(
    projectApi(
      projectId,
      `/conversations/${encodeURIComponent(conversationId)}/review/request-changes`,
    ),
    {
      method: "POST",
      body: JSON.stringify({ message, references }),
    },
  );
}

export function acceptDelivery(
  projectId: string,
  candidateId: string,
): Promise<Record<string, unknown>> {
  return request(projectApi(projectId, `/deliveries/${encodeURIComponent(candidateId)}/accept`), {
    method: "POST",
  });
}

export function discardDelivery(
  projectId: string,
  candidateId: string,
): Promise<Record<string, unknown>> {
  return request(projectApi(projectId, `/deliveries/${encodeURIComponent(candidateId)}/discard`), {
    method: "POST",
  });
}

export function restartSession(
  projectId: string,
  sessionId: string,
): Promise<Record<string, unknown>> {
  return request(projectApi(projectId, `/sessions/${encodeURIComponent(sessionId)}/restart`), {
    method: "POST",
  });
}

export function interruptSession(
  projectId: string,
  sessionId: string,
): Promise<Record<string, unknown>> {
  return request(projectApi(projectId, `/sessions/${encodeURIComponent(sessionId)}/interrupt`), {
    method: "POST",
  });
}

export function getProjectRules(projectId: string): Promise<ProjectRules> {
  return request<ProjectRules>(projectApi(projectId, "/rules"));
}

export function reviseConversationRules(
  projectId: string,
  conversationId: string,
  ruleIds: string[],
): Promise<Record<string, unknown>> {
  return request(
    projectApi(
      projectId,
      `/conversations/${encodeURIComponent(conversationId)}/rules`,
    ),
    {
      method: "PUT",
      body: JSON.stringify({ rule_ids: ruleIds }),
    },
  );
}

export function listRules() {
  return request<import("./types").RuleDefinition[]>("/api/v2/rules");
}

export function createRule(
  body: Omit<import("./types").RuleDefinition, "digest" | "scope">,
) {
  return request<import("./types").RuleDefinition>("/api/v2/rules", {
    method: "POST",
    body: JSON.stringify({ ...body, scope: "user" }),
  });
}

export function uploadRuleSource(file: File): Promise<RuleSource> {
  return request<RuleSource>(
    `/api/v2/rule-sources/upload?filename=${encodeURIComponent(file.name)}`,
    {
      method: "POST",
      headers: { "content-type": file.type || "application/octet-stream" },
      body: file,
    },
  );
}

export function importRuleSource(url: string): Promise<RuleSource> {
  return request<RuleSource>("/api/v2/rule-sources/import-url", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function archiveRule(ruleId: string): Promise<import("./types").RuleDefinition> {
  return request<import("./types").RuleDefinition>(
    `/api/v2/rules/${encodeURIComponent(ruleId)}`,
    { method: "DELETE" },
  );
}

export function restoreRule(ruleId: string): Promise<import("./types").RuleDefinition> {
  return request<import("./types").RuleDefinition>(
    `/api/v2/rules/${encodeURIComponent(ruleId)}/restore`,
    { method: "POST" },
  );
}

export function getProjectSkills(projectId: string): Promise<ProjectSkills> {
  return request<ProjectSkills>(projectApi(projectId, "/skills"));
}

export function searchRemoteSkills(
  query: string,
  provider: "all" | "openai" | "anthropic" = "all",
  cursor?: string | null,
): Promise<RemoteSkillSearch> {
  const params = new URLSearchParams({ q: query, provider, limit: "20" });
  if (cursor) params.set("cursor", cursor);
  return request<RemoteSkillSearch>(
    `/api/v2/skills/remote/search?${params.toString()}`,
  );
}

export function importRemoteSkillSource(body: {
  url: string;
  ref?: string;
  display_name?: string;
}) {
  return request<import("./types").SkillSource>(
    "/api/v2/skill-sources/import-remote",
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function bindProjectSkill(
  projectId: string,
  qualifiedName: string,
  body: { enabled: boolean; required: boolean },
) {
  return request<import("./types").SkillBinding>(
    projectApi(
      projectId,
      `/skills/${encodeURIComponent(qualifiedName)}/binding`,
    ),
    {
      method: "PUT",
      body: JSON.stringify({ scope: "project", ...body }),
    },
  );
}

export function activateConversationSkill(
  projectId: string,
  conversationId: string,
  qualifiedName: string,
) {
  return request<Record<string, unknown>>(
    projectApi(
      projectId,
      `/conversations/${encodeURIComponent(conversationId)}/skills/${encodeURIComponent(
        qualifiedName,
      )}/activate`,
    ),
    { method: "POST" },
  );
}

export function createSkillSource(body: {
  path: string;
  display_name?: string;
  mode: "connect" | "copy";
}) {
  return request<import("./types").SkillSource>("/api/v2/skill-sources", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateSkillSource(
  sourceId: string,
  body: {
    trust_state?: "user_trusted" | "org_trusted" | "untrusted" | "needs_review" | "quarantined";
    enabled?: boolean;
    auto_enable?: boolean;
  },
) {
  return request<import("./types").SkillSource>(
    `/api/v2/skill-sources/${encodeURIComponent(sourceId)}`,
    { method: "PATCH", body: JSON.stringify(body) },
  );
}

export function rescanSkillSource(sourceId: string) {
  return request<import("./types").SkillSource>(
    `/api/v2/skill-sources/${encodeURIComponent(sourceId)}/rescan`,
    { method: "POST" },
  );
}

export function disconnectSkillSource(sourceId: string) {
  return request<import("./types").SkillSource>(
    `/api/v2/skill-sources/${encodeURIComponent(sourceId)}`,
    { method: "DELETE" },
  );
}

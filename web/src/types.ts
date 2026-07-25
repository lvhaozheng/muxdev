export type CaptureGrade = "recorded" | "observed" | "verified";

export interface Project {
  project_id: string;
  name: string;
  path: string;
  status: string;
  available: boolean;
  conversation_count: number;
  needs_you: number;
  active_sessions: number;
  last_opened_at?: string | null;
}

export interface Conversation {
  conversation_id: string;
  title: string;
  goal: string;
  status: string;
  mode: string;
  primary_agent_id?: string | null;
  active_run_id?: string | null;
  active_candidate_id?: string | null;
  updated_at: string;
  metadata?: Record<string, unknown>;
}

export interface ActivityEvent {
  event_id: string;
  conversation_id: string;
  sequence: number;
  schema_version: 2;
  type: string;
  actor: { kind: string; id: string };
  source: {
    run_id?: string | null;
    assignment_id?: string | null;
    session_id?: string | null;
    generation?: number | null;
  };
  occurred_at: string;
  capture_grade: CaptureGrade;
  correlation_id?: string | null;
  payload: Record<string, unknown>;
  previous_hash: string;
  event_hash: string;
}

export interface AgentSession {
  session_id: string;
  agent_id: string;
  status: string;
  generation: number;
  lane_type: string;
  current_assignment_id?: string | null;
  metadata?: Record<string, unknown>;
  attached?: boolean;
  can_resume?: boolean;
  can_restart?: boolean;
  can_interrupt?: boolean;
  failure?: {
    code: string;
    message: string;
    remediation: string;
    retryable: boolean;
  } | null;
}

export interface Assignment {
  assignment_id: string;
  agent_id: string;
  title: string;
  status: string;
  work_mode: string;
  run_id?: string | null;
  dependencies?: string[];
}

export interface ConversationSnapshot {
  schema_version: "muxdev.conversation-snapshot.v1";
  conversation: Conversation;
  attention: string;
  active_turn?: Record<string, unknown> | null;
  participants: Array<Record<string, unknown>>;
  sessions: AgentSession[];
  assignments: Assignment[];
  orchestration_plans: Array<{
    plan_id: string;
    version: number;
    status: string;
    summary: string;
    plan: {
      nodes: Array<{
        id: string;
        title: string;
        agent_id: string;
        dependencies: string[];
        executor_kind?: "agent_session" | "native_subagent";
      }>;
    };
  }>;
  interactions: Array<{
    interaction_id: string;
    prompt: string;
    status: string;
    response?: string | null;
    options?: Array<{
      label: string;
      description?: string;
      recommended?: boolean;
      value?: string;
    }>;
  }>;
  timeline: ActivityEvent[];
  tool_summaries: {
    changes?: { files: number; additions: number; deletions: number };
    verification?: { attempts: number; current: number };
    terminal?: { sessions: number; active: number };
    memory?: {
      checkpoint_id?: string | null;
      version: number;
      through_sequence: number;
    };
    rules?: {
      snapshot_id?: string | null;
      count: number;
      version?: number;
      digest?: string;
      frozen?: RuleDefinition[];
    };
  };
  next_actions: string[];
  last_sequence: number;
}

export interface FileChange {
  change_id: string;
  path: string;
  kind: "add" | "modify" | "delete" | "rename";
  before_hash?: string | null;
  after_hash?: string | null;
  patch: string;
  additions: number;
  deletions: number;
  capture_grade: CaptureGrade;
  created_at: string;
}

export interface ChangeSetView {
  conversation_id: string;
  run_id: string;
  files: FileChange[];
  additions: number;
  deletions: number;
  verified: boolean;
}

export interface VerificationAttempt {
  attempt_id: string;
  command: string[];
  status: string;
  freshness: "current" | "stale" | "superseded";
  duration_ms?: number | null;
  summary: string;
  created_at: string;
}

export interface ReviewView {
  conversation_id: string;
  run_id: string;
  outcome: string;
  review_state: string;
  changes: ChangeSetView;
  verification_attempts: VerificationAttempt[];
  records: ReviewRecord[];
  candidate_id?: string | null;
  available_actions: string[];
}

export interface ReviewRecord {
  review_id: string;
  conversation_id: string;
  run_id?: string | null;
  assignment_id?: string | null;
  session_id?: string | null;
  kind: "interaction" | "decision" | "change_request";
  status: string;
  prompt: string;
  options: Array<Record<string, unknown>>;
  response?: string | null;
  references: Array<{ path?: string; line?: number; end_line?: number }>;
  actor: { kind: string; id: string };
  created_at: string;
  resolved_at?: string | null;
  metadata: Record<string, unknown>;
}

export interface RuleDefinition {
  schema_version: "muxdev.rule.v1";
  rule_id: string;
  version: number;
  title: string;
  description: string;
  kind: "code_standard" | "ci_gate" | "document_template" | "delivery_standard";
  scope: "builtin" | "user";
  workflows: string[];
  path_patterns: string[];
  agent_roles: string[];
  instructions: string;
  enforcement: "advisory" | "required";
  delivery_items: Array<Record<string, unknown>>;
  template?: string | null;
  digest: string;
}

export interface ProjectRules {
  project_id: string;
  bindings: Array<{
    binding: {
      rule_id: string;
      version: number;
      enabled: number | boolean;
      workflows: string[];
    };
    rule: RuleDefinition;
    available: boolean;
  }>;
  library: RuleDefinition[];
  legacy_guidance: {
    path: string;
    available: boolean;
    managed: boolean;
  };
}

export interface AgentDefinition {
  agent_id: string;
  display_name?: string;
  provider_id?: string;
  capability_tags?: string[];
  enabled: boolean;
  installed?: boolean;
  detection?: "builtin" | "path";
  detected_command?: string | null;
  command_candidates?: string[];
  available: boolean;
  availability_reason?: string;
  availability_code?: string;
  remediation?: string;
  can_orchestrate: boolean;
}

import {
  Check,
  CheckCircle,
  Code,
  BookOpenText,
  File,
  Files,
  LinkSimple,
  Plus,
  PuzzlePiece,
  UploadSimple,
  ShieldCheck,
  SidebarSimple,
  TerminalWindow,
  Warning,
  XCircle,
} from "@phosphor-icons/react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { formatTime, labelAttention, labelStatus } from "../labels";
import type {
  ChangeSetView,
  ConversationSnapshot,
  ProjectRules,
  ProjectSkills,
  RemoteSkillSearch,
  RuleDefinition,
  RuleSource,
  ReviewView,
  SkillCatalogItem,
} from "../types";

const TerminalPanel = lazy(() =>
  import("./TerminalPanel").then((module) => ({ default: module.TerminalPanel })),
);

export type ToolCanvasTab =
  | "summary"
  | "changes"
  | "terminal"
  | "review"
  | "rule"
  | "skills";
type Tab = ToolCanvasTab;
type FileView = "diff" | "current" | "baseline";

interface Props {
  projectId: string;
  snapshot: ConversationSnapshot;
  changes?: ChangeSetView;
  review?: ReviewView;
  rules?: ProjectRules;
  skills?: ProjectSkills;
  busy: boolean;
  fileContent?: { content: string; binary: boolean; truncated: boolean };
  onLoadFile: (path: string, view: FileView) => void;
  onAccept: () => void;
  onDiscard: () => void;
  onRequestChanges: (
    message: string,
    path?: string,
    line?: number,
    endLine?: number,
  ) => Promise<void>;
  onReviseRules: (ruleIds: string[]) => Promise<void>;
  onCreateRule: (
    rule: Omit<RuleDefinition, "digest" | "scope">,
  ) => Promise<void>;
  onUploadRuleSource: (file: globalThis.File) => Promise<RuleSource>;
  onImportRuleSource: (url: string) => Promise<RuleSource>;
  onArchiveRule: (ruleId: string) => Promise<void>;
  onRestoreRule: (ruleId: string) => Promise<void>;
  onBindSkill: (
    qualifiedName: string,
    enabled: boolean,
    required: boolean,
  ) => Promise<void>;
  onCreateSkillSource: (
    path: string,
    mode: "connect" | "copy",
  ) => Promise<void>;
  onUpdateSkillSource: (
    sourceId: string,
    update: {
      trust_state?: "user_trusted" | "org_trusted" | "untrusted" | "needs_review" | "quarantined";
      enabled?: boolean;
      auto_enable?: boolean;
    },
  ) => Promise<void>;
  onRescanSkillSource: (sourceId: string) => Promise<void>;
  onDisconnectSkillSource: (sourceId: string) => Promise<void>;
  onSearchRemoteSkills: (
    query: string,
    provider: "all" | "openai" | "anthropic",
    cursor?: string | null,
  ) => Promise<RemoteSkillSearch>;
  onImportRemoteSkill: (url: string, displayName?: string) => Promise<void>;
  requestedTab?: { tab: ToolCanvasTab; requestId: number } | null;
}

const tabs: Array<{ id: Tab; label: string; icon: typeof Files }> = [
  { id: "summary", label: "Summary", icon: SidebarSimple },
  { id: "changes", label: "Changes", icon: Files },
  { id: "terminal", label: "Terminal", icon: TerminalWindow },
  { id: "review", label: "Review", icon: ShieldCheck },
  { id: "rule", label: "Rule", icon: BookOpenText },
  { id: "skills", label: "Skills", icon: PuzzlePiece },
];

const ruleKindLabels: Record<RuleDefinition["kind"], string> = {
  code_standard: "代码规范",
  ci_gate: "CI 门禁",
  document_template: "文档模板",
  delivery_standard: "交付标准",
};

function RuleDetailDialog({
  rule,
  onClose,
}: {
  rule: RuleDefinition;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = () =>
      Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((item) => !item.hasAttribute("disabled"));
    focusable()[0]?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = items[0];
      const last = items.at(-1)!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="rule-detail-dialog"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="rule-detail-title"
        tabIndex={-1}
      >
        <header>
          <div>
            <span className="eyebrow">RULE DETAIL · V{rule.version}</span>
            <h2 id="rule-detail-title">{rule.title || rule.rule_id}</h2>
            <p>{rule.description || "该 Rule 未提供单独摘要。"}</p>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="关闭 Rule 详情"
            onClick={onClose}
          >
            <XCircle aria-hidden="true" />
          </button>
        </header>
        <div className="rule-detail-meta">
          <span><strong>ID</strong>{rule.rule_id}</span>
          <span><strong>类型</strong>{ruleKindLabels[rule.kind]}</span>
          <span><strong>强度</strong>{rule.enforcement === "required" ? "强制" : "建议"}</span>
          <span><strong>范围</strong>{rule.scope === "builtin" ? "内置模板" : "个人规则"}</span>
          <span><strong>状态</strong>{rule.status === "archived" ? "已归档" : rule.status === "deprecated" ? "已弃用" : "可用"}</span>
          <span><strong>Digest</strong><code>{rule.digest || "未提供"}</code></span>
        </div>
        <section>
          <h3>执行要求</h3>
          <pre>{rule.instructions || "未提供执行要求。"}</pre>
        </section>
        {rule.template ? (
          <section>
            <h3>文档模板</h3>
            <pre>{rule.template}</pre>
          </section>
        ) : null}
        <section className="rule-detail-scope">
          <div>
            <h3>适用工作流</h3>
            <p>{rule.workflows.join("、") || "全部工作流"}</p>
          </div>
          <div>
            <h3>路径范围</h3>
            <p>{rule.path_patterns.join("、") || "未限制路径"}</p>
          </div>
          <div>
            <h3>Agent 角色</h3>
            <p>{rule.agent_roles.join("、") || "全部角色"}</p>
          </div>
        </section>
        {rule.delivery_items.length ? (
          <section>
            <h3>交付项</h3>
            <pre>{JSON.stringify(rule.delivery_items, null, 2)}</pre>
          </section>
        ) : null}
        <section>
          <h3>来源与许可证</h3>
          {rule.source_documents.length ? (
            <div className="rule-detail-sources">
              {rule.source_documents.map((source) => (
                <article key={source.source_id}>
                  <strong>{source.display_name}</strong>
                  <small>
                    {source.license || "许可证未声明"}
                    {source.revision ? ` · ${source.revision}` : ""}
                  </small>
                  {source.original_url ? (
                    <a href={source.original_url} target="_blank" rel="noreferrer">
                      查看原始来源
                    </a>
                  ) : null}
                  {source.local_markdown_path ? (
                    <code>{source.local_markdown_path}</code>
                  ) : null}
                </article>
              ))}
            </div>
          ) : (
            <p className="muted">该 Rule 没有外部来源文档。</p>
          )}
        </section>
      </div>
    </div>
  );
}

export function ToolCanvas({
  projectId,
  snapshot,
  changes,
  review,
  rules,
  skills,
  busy,
  fileContent,
  onLoadFile,
  onAccept,
  onDiscard,
  onRequestChanges,
  onReviseRules,
  onCreateRule,
  onUploadRuleSource,
  onImportRuleSource,
  onArchiveRule,
  onRestoreRule,
  onBindSkill,
  onCreateSkillSource,
  onUpdateSkillSource,
  onRescanSkillSource,
  onDisconnectSkillSource,
  onSearchRemoteSkills,
  onImportRemoteSkill,
  requestedTab,
}: Props) {
  const [tab, setTab] = useState<Tab>("summary");
  const [selectedPath, setSelectedPath] = useState("");
  const [fileView, setFileView] = useState<FileView>("diff");
  const [feedback, setFeedback] = useState("");
  const [line, setLine] = useState("");
  const [endLine, setEndLine] = useState("");
  const [selectedRuleIds, setSelectedRuleIds] = useState<string[]>([]);
  const [showRuleForm, setShowRuleForm] = useState(false);
  const [ruleId, setRuleId] = useState("");
  const [ruleTitle, setRuleTitle] = useState("");
  const [ruleInstructions, setRuleInstructions] = useState("");
  const [ruleSources, setRuleSources] = useState<RuleSource[]>([]);
  const [ruleSourceUrl, setRuleSourceUrl] = useState("");
  const [ruleSourceBusy, setRuleSourceBusy] = useState(false);
  const [ruleSourceError, setRuleSourceError] = useState("");
  const [skillSourcePath, setSkillSourcePath] = useState("");
  const [skillSourceMode, setSkillSourceMode] =
    useState<"connect" | "copy">("connect");
  const [skillBusy, setSkillBusy] = useState(false);
  const [skillError, setSkillError] = useState("");
  const [remoteSkillQuery, setRemoteSkillQuery] = useState("");
  const [remoteSkillUrl, setRemoteSkillUrl] = useState("");
  const [remoteSkillProvider, setRemoteSkillProvider] =
    useState<"all" | "openai" | "anthropic">("all");
  const [remoteSkillResult, setRemoteSkillResult] =
    useState<RemoteSkillSearch | null>(null);
  const [remoteSkillBusy, setRemoteSkillBusy] = useState(false);
  const [remoteSkillError, setRemoteSkillError] = useState("");
  const [skillSections, setSkillSections] = useState({
    project: true,
    global: false,
    remote: false,
  });
  const [selectedRule, setSelectedRule] = useState<RuleDefinition | null>(null);
  const ruleReturnFocus = useRef<HTMLElement | null>(null);
  const [ruleKind, setRuleKind] =
    useState<RuleDefinition["kind"]>("code_standard");
  const [ruleEnforcement, setRuleEnforcement] =
    useState<RuleDefinition["enforcement"]>("advisory");
  const currentCandidate =
    review?.candidate_id || snapshot.conversation.active_candidate_id || "";
  const reviewHasContent = Boolean(
    currentCandidate ||
      review?.changes.files.length ||
      review?.verification_attempts.length ||
      review?.records.length,
  );

  useEffect(() => {
    if (requestedTab) setTab(requestedTab.tab);
  }, [requestedTab]);

  function resetRuleForm() {
    setRuleId("");
    setRuleTitle("");
    setRuleInstructions("");
    setRuleSources([]);
    setRuleSourceUrl("");
    setRuleSourceError("");
    setShowRuleForm(false);
  }

  function applyRuleSource(source: RuleSource) {
    setRuleSources((current) => [
      ...current.filter((item) => item.source_id !== source.source_id),
      source,
    ]);
    if (!ruleTitle.trim()) setRuleTitle(source.display_name);
    if (source.markdown) setRuleInstructions(source.markdown);
  }

  function openRuleDetail(rule: RuleDefinition, trigger: HTMLElement) {
    ruleReturnFocus.current = trigger;
    setSelectedRule(rule);
  }

  function closeRuleDetail() {
    setSelectedRule(null);
    window.setTimeout(() => ruleReturnFocus.current?.focus(), 0);
  }

  useEffect(() => {
    if (!selectedPath && changes?.files.length) {
      setSelectedPath(changes.files[0].path);
    }
  }, [changes, selectedPath]);

  useEffect(() => {
    setTab("summary");
    setSelectedPath("");
    setFileView("diff");
    setFeedback("");
    setLine("");
    setEndLine("");
    setSelectedRuleIds(
      (snapshot.tool_summaries.rules?.frozen ?? []).map((rule) => rule.rule_id),
    );
  }, [
    snapshot.conversation.conversation_id,
    snapshot.conversation.active_run_id,
  ]);

  useEffect(() => {
    if (selectedPath && tab === "changes") onLoadFile(selectedPath, fileView);
  }, [fileView, onLoadFile, selectedPath, tab]);

  const activeAssignments = useMemo(
    () =>
      snapshot.assignments.filter(
        (item) =>
          [
            "proposed",
            "queued",
            "running",
            "reported",
            "verifying",
            "ready_to_merge",
            "merging",
          ].includes(item.status),
      ),
    [snapshot.assignments],
  );
  const projectSkillBindings = useMemo(
    () =>
      new Map(
        (skills?.bindings ?? [])
          .filter((item) => item.scope === "project" && Boolean(item.enabled))
          .map((item) => [item.qualified_name, item]),
      ),
    [skills?.bindings],
  );
  const projectSkills = useMemo(
    () =>
      (skills?.catalog ?? []).filter((item) =>
        projectSkillBindings.has(item.qualified_name),
      ),
    [projectSkillBindings, skills?.catalog],
  );
  const unusedGlobalSkills = useMemo(
    () =>
      (skills?.catalog ?? []).filter(
        (item) => !projectSkillBindings.has(item.qualified_name),
      ),
    [projectSkillBindings, skills?.catalog],
  );

  async function runRemoteSkillSearch(cursor?: string | null) {
    setRemoteSkillBusy(true);
    setRemoteSkillError("");
    try {
      const result = await onSearchRemoteSkills(
        remoteSkillQuery.trim(),
        remoteSkillProvider,
        cursor,
      );
      setRemoteSkillResult((current) =>
        cursor && current
          ? {
              ...result,
              items: [
                ...current.items,
                ...result.items.filter(
                  (item) =>
                    !current.items.some(
                      (existing) => existing.catalog_id === item.catalog_id,
                    ),
                ),
              ],
            }
          : result,
      );
    } catch (error) {
      setRemoteSkillError(
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setRemoteSkillBusy(false);
    }
  }

  function isRemoteSkillInstalled(
    repository: string,
    repositoryPath: string,
    commitSha: string,
  ): boolean {
    return (skills?.sources ?? []).some(
      (source) =>
        source.metadata.repository === repository &&
        source.metadata.repository_path === repositoryPath &&
        source.metadata.commit_sha === commitSha,
    );
  }

  function renderCatalogSkill(skill: SkillCatalogItem) {
    const binding = skills?.bindings.find(
      (item) =>
        item.scope === "project" &&
        item.qualified_name === skill.qualified_name,
    );
    const bound = Boolean(binding?.enabled);
    return (
      <article className="skill-card" key={skill.qualified_name}>
        <header>
          <div>
            <strong>{skill.name}</strong>
            <small>{skill.qualified_name} · {skill.source_id}</small>
          </div>
          <span className={`trust-state ${skill.trust}`}>{skill.trust}</span>
        </header>
        <p>{skill.description || "未提供描述"}</p>
        <div className="skill-meta">
          <span>{skill.files.length} 文件</span>
          <span>{skill.script_count} 脚本</span>
          <span>{skill.usage_count} 次加载</span>
          <span>
            Codex {skill.consumer_compatibility.codex === "native" ? "原生" : "审计加载器"}
          </span>
          <span>
            Claude {skill.consumer_compatibility["claude-code"] === "native" ? "原生" : "审计加载器"}
          </span>
          <span>
            Deep Code {skill.consumer_compatibility.deepcode === "native" ? "原生" : "审计加载器"}
          </span>
          {binding?.required ? <span className="required-skill">可信交付必需</span> : null}
        </div>
        {skill.validation_errors.length ? (
          <p className="field-error">{skill.validation_errors.join("；")}</p>
        ) : null}
        {!skill.enabled && !bound ? (
          <p className="skill-review-note">来源尚未完成信任审核，暂不能绑定项目。</p>
        ) : null}
        <footer>
          <code>{skill.revision ? `${skill.revision.slice(0, 24)}…` : "等待审计"}</code>
          <button
            className={bound ? "secondary" : "primary"}
            type="button"
            disabled={skillBusy || (!skill.enabled && !bound)}
            onClick={async () => {
              setSkillBusy(true);
              setSkillError("");
              try {
                await onBindSkill(
                  skill.qualified_name,
                  !bound,
                  !bound ? Boolean(binding?.required) : false,
                );
              } catch (error) {
                setSkillError(
                  error instanceof Error ? error.message : String(error),
                );
              } finally {
                setSkillBusy(false);
              }
            }}
          >
            {bound ? "从项目停用" : "用于当前项目"}
          </button>
          {bound ? (
            <button
              className="text-button"
              type="button"
              disabled={skillBusy}
              onClick={() =>
                void onBindSkill(
                  skill.qualified_name,
                  true,
                  !Boolean(binding?.required),
                )
              }
            >
              {binding?.required ? "取消必需门禁" : "设为必需门禁"}
            </button>
          ) : null}
        </footer>
      </article>
    );
  }

  return (
    <aside className="tool-canvas" aria-label="上下文工具画布">
      <nav className="tool-tabs" aria-label="Conversation 工具">
        {tabs.map((item) => {
          const Icon = item.icon;
          const count =
            item.id === "changes"
              ? changes?.files.length
              : item.id === "terminal"
                ? snapshot.sessions.length
                : item.id === "review"
                  ? review?.records.length
                  : item.id === "rule"
                    ? snapshot.tool_summaries.rules?.count
                    : item.id === "skills"
                      ? skills?.catalog.filter((skill) => skill.enabled).length
                      : undefined;
          return (
            <button
              type="button"
              aria-label={item.label}
              aria-current={tab === item.id ? "page" : undefined}
              title={item.label}
              className={tab === item.id ? "active" : ""}
              key={item.id}
              onClick={() => setTab(item.id)}
            >
              <Icon aria-hidden="true" />
              <span>{item.label}</span>
              {count ? <small>{count}</small> : null}
            </button>
          );
        })}
      </nav>
      <div className="tool-body">
        {tab === "summary" ? (
          <section className="summary-panel">
            <header className="tool-section-head">
              <div>
                <span className="eyebrow">THIS SESSION</span>
                <h2>
                  {snapshot.attention_detail?.label ||
                    labelStatus(snapshot.conversation.status)}
                </h2>
              </div>
              <span className={`status-pill ${snapshot.attention}`}>
                {labelAttention(snapshot.attention)}
              </span>
            </header>
            <div className="summary-metrics">
              <article>
                <Files />
                <strong>{snapshot.tool_summaries.changes?.files ?? 0}</strong>
                <span>变更文件</span>
              </article>
              <article>
                <ShieldCheck />
                <strong>{snapshot.tool_summaries.verification?.current ?? 0}</strong>
                <span>当前验证</span>
              </article>
              <article>
                <TerminalWindow />
                <strong>{snapshot.tool_summaries.terminal?.active ?? 0}</strong>
                <span>活跃 Session</span>
              </article>
            </div>
            <section className="panel-note">
              <Code />
              共享记忆 v{snapshot.tool_summaries.memory?.version ?? 0}
              {" · "}
              已冻结 {snapshot.tool_summaries.rules?.count ?? 0} 条 Rule
            </section>
            {snapshot.attention_detail?.action === "open_terminal" ? (
              <section className="attention-summary-card" role="alert">
                <Warning aria-hidden="true" />
                <div>
                  <strong>{snapshot.attention_detail.title}</strong>
                  <p>{snapshot.attention_detail.message}</p>
                  <small>{snapshot.attention_detail.remediation}</small>
                </div>
                <button
                  className="primary"
                  type="button"
                  onClick={() => setTab("terminal")}
                >
                  <TerminalWindow aria-hidden="true" />
                  打开 Terminal
                </button>
              </section>
            ) : null}
            <section className="panel-section">
              <h3>可见 Fleet</h3>
              {snapshot.assignments.map((assignment) => {
                const metadata =
                  assignment.metadata && typeof assignment.metadata === "object"
                    ? assignment.metadata
                    : {};
                const waitingReason =
                  metadata.waiting_reason &&
                  typeof metadata.waiting_reason === "object"
                    ? (metadata.waiting_reason as Record<string, unknown>)
                    : null;
                return (
                <div className="summary-row" key={assignment.assignment_id}>
                  <span className={`state-dot ${assignment.status}`} />
                  <div>
                    <strong>{assignment.agent_id}</strong>
                    <small>{assignment.title}</small>
                    {waitingReason?.message ? (
                      <small className="summary-waiting-reason">
                        {String(waitingReason.message)}
                      </small>
                    ) : null}
                  </div>
                  <span>{labelStatus(assignment.status)}</span>
                </div>
                );
              })}
              {!snapshot.assignments.length ? (
                <p className="muted">尚未分配执行任务。</p>
              ) : null}
            </section>
            {activeAssignments.length ? (
              <section className="panel-note">
                <span className="live-dot" />
                Agent 正在工作，可随时从 Composer 继续补充上下文。
              </section>
            ) : null}
          </section>
        ) : null}

        {tab === "changes" ? (
          <section className="changes-panel">
            <header className="tool-section-head">
              <div>
                <span className="eyebrow">VERIFIED CHANGESET</span>
                <h2>本轮变更 {changes?.files.length ?? 0} 个文件</h2>
              </div>
              <span className="diff-stats">
                +{changes?.additions ?? 0} −{changes?.deletions ?? 0}
              </span>
            </header>
            <div className="change-list">
              {changes?.files.map((change) => (
                <button
                  type="button"
                  key={change.change_id}
                  className={selectedPath === change.path ? "selected" : ""}
                  onClick={() => {
                    setSelectedPath(change.path);
                    onLoadFile(change.path, fileView);
                  }}
                >
                  <File />
                  <span>{change.path}</span>
                  <small>
                    +{change.additions} −{change.deletions}
                  </small>
                </button>
              ))}
              {!changes?.files.length ? (
                <div className="tool-empty compact-empty">
                  <Files />
                  <strong>没有已验证的文件变化</strong>
                  <p>运行期观察不会冒充最终对账结果。</p>
                </div>
              ) : null}
            </div>
            {selectedPath ? (
              <section className="file-peek">
                <header>
                  <strong>{selectedPath}</strong>
                  <div className="segmented">
                    {(["diff", "current", "baseline"] as FileView[]).map((value) => (
                      <button
                        type="button"
                        key={value}
                        className={fileView === value ? "active" : ""}
                        onClick={() => setFileView(value)}
                      >
                        {value}
                      </button>
                    ))}
                  </div>
                </header>
                <pre>
                  <code>
                    {fileContent?.binary
                      ? "Binary content is metadata-only."
                      : fileContent?.content || "正在读取文件视图…"}
                  </code>
                </pre>
                {fileContent?.truncated ? (
                  <p className="peek-note">预览已在 1 MiB 处截断。</p>
                ) : null}
              </section>
            ) : null}
          </section>
        ) : null}

        {tab === "terminal" ? (
          <Suspense fallback={<div className="tool-empty">正在加载终端画布…</div>}>
            <TerminalPanel projectId={projectId} sessions={snapshot.sessions} />
          </Suspense>
        ) : null}

        {tab === "review" ? (
          <section className="review-panel">
            <header className="review-outcome">
              {review?.review_state === "answered" ? <CheckCircle /> : <ShieldCheck />}
              <div>
                <span className="eyebrow">REVIEW OUTCOME</span>
                <h2>
                  {reviewHasContent
                    ? review?.outcome || "交付评审"
                    : snapshot.attention_detail
                      ? "尚未进入交付评审"
                      : "等待 Agent 形成交付候选"}
                </h2>
              </div>
            </header>
            {!reviewHasContent ? (
              <section className="review-not-ready">
                <Warning aria-hidden="true" />
                <div>
                  <strong>
                    {snapshot.attention_detail?.title ||
                      "当前还没有可以评审的交付内容"}
                  </strong>
                  <p>
                    {snapshot.attention_detail?.message ||
                      "Agent 完成工作并提交交付候选后，这里才会出现差异、验证和人工评审记录。"}
                  </p>
                  {snapshot.attention_detail?.remediation ? (
                    <small>{snapshot.attention_detail.remediation}</small>
                  ) : null}
                </div>
                {snapshot.attention_detail?.action === "open_terminal" ? (
                  <button
                    className="primary"
                    type="button"
                    onClick={() => setTab("terminal")}
                  >
                    <TerminalWindow aria-hidden="true" />
                    打开 Terminal
                  </button>
                ) : null}
              </section>
            ) : null}
            <section className="panel-section">
              <h3>人工记录</h3>
              {review?.records.map((record) => (
                <article className="review-record" key={record.review_id}>
                  <header>
                    <strong>
                      {record.kind === "interaction"
                        ? "人工交互"
                        : record.kind === "decision"
                          ? "交付决策"
                          : "修改请求"}
                    </strong>
                    <time>{formatTime(record.created_at)}</time>
                  </header>
                  <p>{record.prompt}</p>
                  {record.response ? <blockquote>{record.response}</blockquote> : null}
                  {record.references.length ? (
                    <small>
                      {record.references
                        .map((reference) =>
                          [
                            reference.path,
                            reference.line
                              ? `L${reference.line}${
                                  reference.end_line
                                    ? `–${reference.end_line}`
                                    : ""
                                }`
                              : "",
                          ]
                            .filter(Boolean)
                            .join(":"),
                        )
                        .join("，")}
                    </small>
                  ) : null}
                </article>
              ))}
              {!review?.records.length ? (
                <p className="muted">
                  {reviewHasContent
                    ? "尚无人工交互、交付决策或修改请求。"
                    : "尚未形成交付候选，因此没有人工评审内容。"}
                </p>
              ) : null}
            </section>
            <section className="panel-section">
              <h3>验证历史</h3>
              {review?.verification_attempts.map((attempt) => (
                <article className={`verification-row ${attempt.freshness}`} key={attempt.attempt_id}>
                  {attempt.status === "passed" ? <Check /> : <XCircle />}
                  <div>
                    <code>{attempt.command.join(" ")}</code>
                    <small>
                      {formatTime(attempt.created_at)}
                      {attempt.duration_ms ? ` · ${attempt.duration_ms}ms` : ""}
                    </small>
                  </div>
                  <span>{attempt.freshness === "current" ? labelStatus(attempt.status) : labelStatus(attempt.freshness)}</span>
                </article>
              ))}
              {!review?.verification_attempts.length ? (
                <p className="muted">
                  {reviewHasContent
                    ? "本轮尚无 Runtime 验证记录。"
                    : "任务尚未到达验证阶段，因此没有 Runtime 验证记录。"}
                </p>
              ) : null}
            </section>
            {reviewHasContent &&
            currentCandidate &&
            review?.review_state === "pending" ? (
              <section className="review-feedback">
                <label>
                  <span>修改意见</span>
                  <textarea
                    value={feedback}
                    onChange={(event) => setFeedback(event.target.value)}
                    rows={4}
                    placeholder="说明需要调整的地方；当前选中文件会作为引用附上。"
                  />
                </label>
                {selectedPath ? (
                  <div className="line-reference">
                    <span>{selectedPath}</span>
                    <label>
                      起始行
                      <input
                        type="number"
                        min="1"
                        value={line}
                        onChange={(event) => setLine(event.target.value)}
                      />
                    </label>
                    <label>
                      结束行
                      <input
                        type="number"
                        min={line || "1"}
                        value={endLine}
                        onChange={(event) => setEndLine(event.target.value)}
                      />
                    </label>
                  </div>
                ) : null}
                <button
                  className="secondary"
                  type="button"
                  disabled={!feedback.trim() || busy}
                  onClick={async () => {
                    try {
                      await onRequestChanges(
                        feedback.trim(),
                        selectedPath || undefined,
                        line ? Number(line) : undefined,
                        endLine ? Number(endLine) : undefined,
                      );
                      setFeedback("");
                    } catch {
                      // The mutation owns the visible error toast; keep the draft for retry.
                    }
                  }}
                >
                  <Warning />
                  请求修改
                </button>
              </section>
            ) : null}
          </section>
        ) : null}

        {tab === "rule" ? (
          <section className="rule-panel">
            <header className="tool-section-head">
              <div>
                <span className="eyebrow">TRUSTED DELIVERY RULES</span>
                <h2>Conversation 冻结规则</h2>
              </div>
              <span className="status-pill">
                {snapshot.tool_summaries.rules?.count ?? 0} 条
              </span>
            </header>
            <button
              className="secondary rule-create-toggle"
              type="button"
              aria-expanded={showRuleForm}
              onClick={() => setShowRuleForm((current) => !current)}
            >
              <Plus />
              自定义 Rule
            </button>
            {showRuleForm ? (
              <form
                className="rule-create-form"
                onSubmit={async (event) => {
                  event.preventDefault();
                  const normalizedId = ruleId.trim();
                  const normalizedTitle = ruleTitle.trim();
                  const normalizedInstructions = ruleInstructions.trim();
                  if (!normalizedId || !normalizedTitle || !normalizedInstructions) return;
                  await onCreateRule({
                    schema_version: "muxdev.rule.v1",
                    rule_id: normalizedId,
                    version: 1,
                    title: normalizedTitle,
                    description: normalizedInstructions.slice(0, 240),
                    kind: ruleKind,
                    workflows: ["change"],
                    path_patterns: [],
                    agent_roles: [],
                    instructions: normalizedInstructions,
                    enforcement: ruleEnforcement,
                    delivery_items: [],
                    source_documents: ruleSources.map(({ markdown: _markdown, ...source }) => source),
                    template:
                      ruleKind === "document_template"
                        ? normalizedInstructions
                        : null,
                  });
                  resetRuleForm();
                }}
              >
                <section className="rule-source-import" aria-label="Rule 来源">
                  <div className="rule-source-actions">
                    <label className="secondary file-upload-button">
                      <UploadSimple />
                      上传 MD / TXT / HTML
                      <input
                        type="file"
                        accept=".md,.markdown,.txt,.html,.htm,text/markdown,text/plain,text/html"
                        disabled={ruleSourceBusy}
                        onChange={async (event) => {
                          const sourceFile = event.target.files?.[0];
                          event.target.value = "";
                          if (!sourceFile) return;
                          setRuleSourceBusy(true);
                          setRuleSourceError("");
                          try {
                            applyRuleSource(await onUploadRuleSource(sourceFile));
                          } catch (error) {
                            setRuleSourceError(
                              error instanceof Error ? error.message : String(error),
                            );
                          } finally {
                            setRuleSourceBusy(false);
                          }
                        }}
                      />
                    </label>
                    <div className="rule-url-import">
                      <LinkSimple aria-hidden="true" />
                      <input
                        type="url"
                        value={ruleSourceUrl}
                        placeholder="https://example.com/rules"
                        aria-label="公开规则网页 URL"
                        disabled={ruleSourceBusy}
                        onChange={(event) => setRuleSourceUrl(event.target.value)}
                      />
                      <button
                        className="secondary"
                        type="button"
                        disabled={ruleSourceBusy || !ruleSourceUrl.trim()}
                        onClick={async () => {
                          setRuleSourceBusy(true);
                          setRuleSourceError("");
                          try {
                            applyRuleSource(
                              await onImportRuleSource(ruleSourceUrl.trim()),
                            );
                            setRuleSourceUrl("");
                          } catch (error) {
                            setRuleSourceError(
                              error instanceof Error ? error.message : String(error),
                            );
                          } finally {
                            setRuleSourceBusy(false);
                          }
                        }}
                      >
                        导入网页
                      </button>
                    </div>
                  </div>
                  {ruleSourceError ? (
                    <p className="field-error" role="alert">{ruleSourceError}</p>
                  ) : null}
                  {ruleSources.map((source) => (
                    <details className="rule-source-preview" key={source.source_id}>
                      <summary>
                        {source.display_name} · {source.kind === "url" ? "网页" : "文件"}
                      </summary>
                      <p>
                        已保存为 {source.local_markdown_path}
                        {source.digest ? ` · ${source.digest.slice(0, 20)}…` : ""}
                      </p>
                      <small>
                        抓取时间：{source.captured_at
                          ? new Date(source.captured_at).toLocaleString("zh-CN")
                          : "未知"}
                        {" · "}许可证：{source.license || "来源未声明"}
                        {source.markdown
                          ? ` · 摘要：${source.markdown.replace(/\s+/g, " ").slice(0, 120)}`
                          : ""}
                      </small>
                      {source.original_url ? (
                        <a href={source.original_url} target="_blank" rel="noreferrer">
                          {source.original_url}
                        </a>
                      ) : null}
                      {source.markdown ? <pre>{source.markdown}</pre> : null}
                    </details>
                  ))}
                  <small>
                    网页导入仅访问公开静态 HTTP(S) 页面；登录态和动态页面请导出后上传。
                  </small>
                </section>
                <label>
                  Rule ID
                  <input
                    value={ruleId}
                    pattern="[A-Za-z0-9][A-Za-z0-9_.-]*"
                    placeholder="team.python-style"
                    maxLength={80}
                    onChange={(event) => setRuleId(event.target.value)}
                    required
                  />
                </label>
                <label>
                  标题
                  <input
                    value={ruleTitle}
                    placeholder="Python 代码规范"
                    maxLength={120}
                    onChange={(event) => setRuleTitle(event.target.value)}
                    required
                  />
                </label>
                <div className="rule-create-options">
                  <label>
                    类型
                    <select
                      value={ruleKind}
                      onChange={(event) =>
                        setRuleKind(event.target.value as RuleDefinition["kind"])
                      }
                    >
                      <option value="code_standard">代码规范</option>
                      <option value="delivery_standard">交付标准</option>
                      <option value="document_template">文档模板</option>
                    </select>
                  </label>
                  <label>
                    强度
                    <select
                      value={ruleEnforcement}
                      onChange={(event) =>
                        setRuleEnforcement(
                          event.target.value as RuleDefinition["enforcement"],
                        )
                      }
                    >
                      <option value="advisory">建议</option>
                      <option value="required">强制</option>
                    </select>
                  </label>
                </div>
                <label>
                  门禁内容与交付标准
                  <textarea
                    value={ruleInstructions}
                    placeholder="说明适用范围、执行要求、完成条件和证明方式。"
                    rows={5}
                    maxLength={262144}
                    onChange={(event) => setRuleInstructions(event.target.value)}
                    required
                  />
                </label>
                <small>
                  CI 门禁只能由已登记的 argv 验证命令创建，不接受网页任意 Shell。
                </small>
                <div className="rule-form-actions">
                  <button
                    className="secondary"
                    type="button"
                    onClick={resetRuleForm}
                    disabled={ruleSourceBusy}
                  >
                    取消
                  </button>
                  <button className="primary" type="submit" disabled={busy || ruleSourceBusy}>
                    保存到个人 Rule 库
                  </button>
                </div>
              </form>
            ) : null}
            {(rules?.library ?? []).map((rule) => {
              const binding = rules?.bindings.find(
                (item) =>
                  item.binding.rule_id === rule.rule_id &&
                  item.binding.version === rule.version,
              );
              const frozen = (snapshot.tool_summaries.rules?.frozen ?? []).some(
                (item) =>
                  item.rule_id === rule.rule_id && item.version === rule.version,
              );
              return (
              <article className="rule-card" key={`${rule.rule_id}@${rule.version}`}>
                <header>
                  <button
                    className="rule-card-open"
                    type="button"
                    aria-label={`查看 Rule：${rule.title || rule.rule_id}`}
                    onClick={(event) => openRuleDetail(rule, event.currentTarget)}
                  >
                    <strong>{rule.title || rule.rule_id}</strong>
                    <small>
                      v{rule.version} · {rule.enforcement === "required" ? "强制" : "建议"}
                    </small>
                  </button>
                  <label className="rule-toggle">
                    <input
                      type="checkbox"
                      checked={selectedRuleIds.includes(rule.rule_id)}
                      onChange={(event) =>
                        setSelectedRuleIds((current) =>
                          event.target.checked
                            ? [...current, rule.rule_id]
                            : current.filter((item) => item !== rule.rule_id),
                        )
                      }
                    />
                    <span>
                      {frozen
                        ? "会话已冻结"
                        : binding
                          ? "项目已启用"
                          : rule.scope === "builtin"
                            ? "内置模板"
                            : "个人规则"}
                    </span>
                  </label>
                </header>
                <button
                  className="rule-card-summary"
                  type="button"
                  onClick={(event) => openRuleDetail(rule, event.currentTarget)}
                >
                  <span>{rule.description || rule.instructions}</span>
                  {rule.source_documents?.length ? (
                    <small className="rule-provenance">
                      来源：{rule.source_documents.map((source) => source.display_name).join("、")}
                      {rule.source_documents.some((source) => source.revision)
                        ? ` · 固定版本 ${rule.source_documents
                            .map((source) => source.revision?.slice(0, 8))
                            .filter(Boolean)
                            .join("、")}`
                        : ""}
                    </small>
                  ) : null}
                </button>
                <footer>
                  <span>{rule.kind}</span>
                  <span>{rule.workflows.join(" / ") || "全部流程"}</span>
                  {rule.scope === "user" ? (
                    <button
                      className="text-button"
                      type="button"
                      disabled={busy}
                      onClick={() => void onArchiveRule(rule.rule_id)}
                    >
                      归档
                    </button>
                  ) : null}
                </footer>
              </article>
              );
            })}
            {!rules?.library.length ? (
              <div className="tool-empty compact-empty">
                <BookOpenText />
                <strong>项目尚未绑定 Rule</strong>
                <p>可从内置模板或个人 Rule 库选择规范，再冻结到 Conversation。</p>
              </div>
            ) : null}
            {rules?.library.length ? (
              <button
                className="primary rule-apply"
                type="button"
                disabled={busy}
                onClick={() => void onReviseRules(selectedRuleIds)}
              >
                冻结为新 Rule 版本
              </button>
            ) : null}
            {rules?.legacy_guidance.available ? (
              <section className="panel-note">
                MUXDEV.md 已作为 legacy advisory Rule 注入，不受系统自动覆盖。
              </section>
            ) : null}
            {rules?.archived_library?.length ? (
              <details className="archived-rule-list">
                <summary>已归档个人 Rule（{rules.archived_library.length}）</summary>
                {rules.archived_library.map((rule) => (
                  <article className="rule-card archived" key={`${rule.rule_id}@${rule.version}`}>
                    <button
                      className="rule-card-open"
                      type="button"
                      onClick={(event) => openRuleDetail(rule, event.currentTarget)}
                    >
                      <strong>{rule.title}</strong>
                      <small>v{rule.version} · 已归档</small>
                    </button>
                    <button
                      className="rule-card-summary"
                      type="button"
                      onClick={(event) => openRuleDetail(rule, event.currentTarget)}
                    >
                      <span>{rule.description || rule.instructions}</span>
                    </button>
                    <button
                      className="secondary"
                      type="button"
                      disabled={busy}
                      onClick={() => void onRestoreRule(rule.rule_id)}
                    >
                      恢复
                    </button>
                  </article>
                ))}
              </details>
            ) : null}
          </section>
        ) : null}
        {tab === "skills" ? (
          <section className="skills-panel">
            <header className="tool-section-head">
              <div>
                <span className="eyebrow">AUDITED SKILL CATALOG</span>
                <h2>Skills 使用与发现</h2>
              </div>
              <span className="status-pill">
                {projectSkills.length} 项目使用中
              </span>
            </header>
            {skillError ? (
              <p className="field-error" role="alert">{skillError}</p>
            ) : null}

            <section className="skill-category">
              <button
                className="skill-category-toggle"
                type="button"
                aria-expanded={skillSections.project}
                aria-controls="project-skills"
                onClick={() =>
                  setSkillSections((current) => ({
                    ...current,
                    project: !current.project,
                  }))
                }
              >
                <span>
                  <strong>项目使用中</strong>
                  <small>这些 Skills 会进入当前项目的冻结和可信交付流程。</small>
                </span>
                <span>{projectSkills.length}</span>
              </button>
              {skillSections.project ? (
                <div className="skill-category-body" id="project-skills">
                  {projectSkills.map(renderCatalogSkill)}
                  {!projectSkills.length ? (
                    <div className="tool-empty compact-empty">
                      <PuzzlePiece />
                      <strong>当前项目尚未启用 Skill</strong>
                      <p>可从全局目录选择，审核后绑定到当前项目。</p>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </section>

            <section className="skill-category">
              <button
                className="skill-category-toggle"
                type="button"
                aria-expanded={skillSections.global}
                aria-controls="global-skills"
                onClick={() =>
                  setSkillSections((current) => ({
                    ...current,
                    global: !current.global,
                  }))
                }
              >
                <span>
                  <strong>全局已安装、当前项目未使用</strong>
                  <small>包含内置、个人目录和等待信任审核的托管副本。</small>
                </span>
                <span>{unusedGlobalSkills.length}</span>
              </button>
              {skillSections.global ? (
                <div className="skill-category-body" id="global-skills">
                  {unusedGlobalSkills.map(renderCatalogSkill)}
                  {!unusedGlobalSkills.length ? (
                    <p className="muted">全局目录中的 Skills 已全部用于当前项目。</p>
                  ) : null}
                  <details className="skill-source-manager">
                    <summary>来源连接、信任与文件审计（{skills?.sources.length ?? 0}）</summary>
                    <form
                      onSubmit={async (event) => {
                        event.preventDefault();
                        if (!skillSourcePath.trim()) return;
                        setSkillBusy(true);
                        setSkillError("");
                        try {
                          await onCreateSkillSource(
                            skillSourcePath.trim(),
                            skillSourceMode,
                          );
                          setSkillSourcePath("");
                        } catch (error) {
                          setSkillError(
                            error instanceof Error ? error.message : String(error),
                          );
                        } finally {
                          setSkillBusy(false);
                        }
                      }}
                    >
                      <input
                        value={skillSourcePath}
                        aria-label="Skill 来源目录"
                        placeholder="本地 Skills 目录"
                        onChange={(event) => setSkillSourcePath(event.target.value)}
                      />
                      <select
                        aria-label="Skill 来源模式"
                        value={skillSourceMode}
                        onChange={(event) =>
                          setSkillSourceMode(event.target.value as "connect" | "copy")
                        }
                      >
                        <option value="connect">只读连接</option>
                        <option value="copy">复制到 MuxDev</option>
                      </select>
                      <button
                        className="secondary"
                        type="submit"
                        disabled={skillBusy || !skillSourcePath.trim()}
                      >
                        连接
                      </button>
                    </form>
                    {(skills?.sources ?? []).map((source) => (
                      <article className="skill-source-card" key={source.source_id}>
                        <header>
                          <div>
                            <strong>{source.display_name}</strong>
                            <small>
                              {source.metadata.remote
                                ? `${source.metadata.repository} · 固定 ${source.metadata.commit_sha?.slice(0, 8)}`
                                : source.mode === "copy"
                                  ? "托管副本"
                                  : "只读连接"}
                            </small>
                          </div>
                          <span className={`trust-state ${source.trust_state}`}>
                            {source.trust_state}
                          </span>
                        </header>
                        <p>{source.metadata.origin_url || source.path}</p>
                        <small>
                          {source.metadata.skill_count ?? 0} Skills ·{" "}
                          {source.metadata.file_count ?? 0} 文件
                          {source.metadata.license ? ` · ${source.metadata.license}` : ""}
                          {source.metadata.drifted ? " · 发现新版本" : ""}
                        </small>
                        <footer>
                          <button
                            className="text-button"
                            type="button"
                            disabled={skillBusy || source.status === "disconnected"}
                            onClick={async () => {
                              setSkillBusy(true);
                              try {
                                await onRescanSkillSource(source.source_id);
                              } finally {
                                setSkillBusy(false);
                              }
                            }}
                          >
                            重新扫描
                          </button>
                          {source.trust_state !== "user_trusted" ? (
                            <button
                              className="secondary"
                              type="button"
                              disabled={skillBusy || source.status === "disconnected"}
                              onClick={() =>
                                void onUpdateSkillSource(source.source_id, {
                                  trust_state: "user_trusted",
                                  enabled: true,
                                })
                              }
                            >
                              审核后信任并启用
                            </button>
                          ) : (
                            <button
                              className="secondary"
                              type="button"
                              disabled={skillBusy || source.status === "disconnected"}
                              onClick={() =>
                                void onUpdateSkillSource(source.source_id, {
                                  enabled: !source.enabled,
                                })
                              }
                            >
                              {source.enabled ? "停用来源" : "启用来源"}
                            </button>
                          )}
                          {source.status !== "disconnected" ? (
                            <button
                              className="text-button danger"
                              type="button"
                              disabled={skillBusy}
                              onClick={async () => {
                                setSkillBusy(true);
                                try {
                                  await onDisconnectSkillSource(source.source_id);
                                } finally {
                                  setSkillBusy(false);
                                }
                              }}
                            >
                              断开连接
                            </button>
                          ) : (
                            <span className="status-pill">已断开</span>
                          )}
                        </footer>
                        {source.metadata.files?.length ? (
                          <details>
                            <summary>文件审计</summary>
                            <ul>
                              {source.metadata.files.slice(0, 100).map((file) => (
                                <li key={file.path}>
                                  <code>{file.path}</code>
                                  <span>{file.size_bytes} B · {file.digest.slice(0, 18)}…</span>
                                </li>
                              ))}
                            </ul>
                          </details>
                        ) : null}
                      </article>
                    ))}
                  </details>
                </div>
              ) : null}
            </section>

            <section className="skill-category">
              <button
                className="skill-category-toggle"
                type="button"
                aria-expanded={skillSections.remote}
                aria-controls="remote-skills"
                onClick={() =>
                  setSkillSections((current) => ({
                    ...current,
                    remote: !current.remote,
                  }))
                }
              >
                <span>
                  <strong>联网搜索与下载</strong>
                  <small>搜索 OpenAI、Anthropic 官方目录，或导入 GitHub Skill 目录。</small>
                </span>
                <span>{remoteSkillResult?.total ?? "在线"}</span>
              </button>
              {skillSections.remote ? (
                <div className="skill-category-body remote-skill-catalog" id="remote-skills">
                  <form
                    className="remote-skill-search"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void runRemoteSkillSearch();
                    }}
                  >
                    <input
                      type="search"
                      aria-label="搜索联网 Skills"
                      value={remoteSkillQuery}
                      placeholder="按名称或目录搜索"
                      onChange={(event) => setRemoteSkillQuery(event.target.value)}
                    />
                    <select
                      aria-label="联网 Skill 来源"
                      value={remoteSkillProvider}
                      onChange={(event) =>
                        setRemoteSkillProvider(
                          event.target.value as "all" | "openai" | "anthropic",
                        )
                      }
                    >
                      <option value="all">全部官方目录</option>
                      <option value="openai">OpenAI</option>
                      <option value="anthropic">Anthropic</option>
                    </select>
                    <button className="primary" type="submit" disabled={remoteSkillBusy}>
                      {remoteSkillBusy ? "搜索中…" : "搜索"}
                    </button>
                  </form>
                  <form
                    className="remote-skill-url"
                    onSubmit={async (event) => {
                      event.preventDefault();
                      if (!remoteSkillUrl.trim()) return;
                      setRemoteSkillBusy(true);
                      setRemoteSkillError("");
                      try {
                        await onImportRemoteSkill(remoteSkillUrl.trim());
                        setRemoteSkillUrl("");
                      } catch (error) {
                        setRemoteSkillError(
                          error instanceof Error ? error.message : String(error),
                        );
                      } finally {
                        setRemoteSkillBusy(false);
                      }
                    }}
                  >
                    <LinkSimple aria-hidden="true" />
                    <input
                      type="url"
                      aria-label="GitHub Skill 目录 URL"
                      value={remoteSkillUrl}
                      placeholder="https://github.com/owner/repo/tree/main/path"
                      onChange={(event) => setRemoteSkillUrl(event.target.value)}
                    />
                    <button
                      className="secondary"
                      type="submit"
                      disabled={remoteSkillBusy || !remoteSkillUrl.trim()}
                    >
                      下载目录
                    </button>
                  </form>
                  {remoteSkillError ? (
                    <p className="field-error" role="alert">{remoteSkillError}</p>
                  ) : null}
                  <div className="remote-skill-results" aria-live="polite">
                    {remoteSkillResult?.items.map((item) => {
                      const installed = isRemoteSkillInstalled(
                        item.repository,
                        item.path,
                        item.commit_sha,
                      );
                      return (
                        <article className="remote-skill-card" key={item.catalog_id}>
                          <header>
                            <div>
                              <strong>{item.name}</strong>
                              <small>{item.provider_label} · {item.repository}</small>
                            </div>
                            <span className="trust-state publisher_verified">官方发布者</span>
                          </header>
                          <p>{item.description || "该 Skill 未提供描述。"}</p>
                          <div className="skill-meta">
                            <span>{item.file_count} 文件</span>
                            <span>{item.script_count} 脚本</span>
                            <span>{item.license || "许可证未声明"}</span>
                            <span>固定 {item.commit_sha.slice(0, 8)}</span>
                          </div>
                          <footer>
                            <a href={item.source_url} target="_blank" rel="noreferrer">
                              查看来源
                            </a>
                            <button
                              className={installed ? "secondary" : "primary"}
                              type="button"
                              disabled={remoteSkillBusy || installed}
                              onClick={async () => {
                                setRemoteSkillBusy(true);
                                setRemoteSkillError("");
                                try {
                                  await onImportRemoteSkill(
                                    item.source_url,
                                    item.name,
                                  );
                                } catch (error) {
                                  setRemoteSkillError(
                                    error instanceof Error ? error.message : String(error),
                                  );
                                } finally {
                                  setRemoteSkillBusy(false);
                                }
                              }}
                            >
                              {installed ? "已下载，等待审核" : "下载到全局"}
                            </button>
                          </footer>
                        </article>
                      );
                    })}
                    {remoteSkillResult && !remoteSkillResult.items.length ? (
                      <div className="tool-empty compact-empty">
                        <PuzzlePiece />
                        <strong>没有匹配的官方 Skill</strong>
                        <p>可调整关键词，或粘贴明确的 GitHub Skill 目录 URL。</p>
                      </div>
                    ) : null}
                  </div>
                  {remoteSkillResult?.next_cursor ? (
                    <button
                      className="secondary remote-load-more"
                      type="button"
                      disabled={remoteSkillBusy}
                      onClick={() =>
                        void runRemoteSkillSearch(remoteSkillResult.next_cursor)
                      }
                    >
                      加载更多
                    </button>
                  ) : null}
                </div>
              ) : null}
            </section>

            <section className="panel-note">
              Skill 仅提供指令内容，不能扩大当前任务权限；联网下载不会自动信任或绑定项目，
              scripts 也不会自动执行。
            </section>
          </section>
        ) : null}
      </div>
      {currentCandidate && review?.review_state === "pending" ? (
        <footer className="action-bar">
          <button className="secondary" type="button" onClick={onDiscard} disabled={busy}>
            回退本轮
          </button>
          <button
            className="secondary"
            type="button"
            onClick={() => setTab("review")}
            disabled={busy}
          >
            请求修改
          </button>
          <button className="primary dark" type="button" onClick={onAccept} disabled={busy}>
            <CheckCircle />
            接受变更
          </button>
        </footer>
      ) : null}
      {selectedRule ? (
        <RuleDetailDialog rule={selectedRule} onClose={closeRuleDetail} />
      ) : null}
    </aside>
  );
}

import {
  Check,
  CheckCircle,
  Code,
  BookOpenText,
  File,
  Files,
  Plus,
  ShieldCheck,
  SidebarSimple,
  TerminalWindow,
  Warning,
  XCircle,
} from "@phosphor-icons/react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { formatTime, labelAttention, labelStatus } from "../labels";
import type {
  ChangeSetView,
  ConversationSnapshot,
  ProjectRules,
  RuleDefinition,
  ReviewView,
} from "../types";

const TerminalPanel = lazy(() =>
  import("./TerminalPanel").then((module) => ({ default: module.TerminalPanel })),
);

type Tab = "summary" | "changes" | "terminal" | "review" | "rule";
type FileView = "diff" | "current" | "baseline";

interface Props {
  projectId: string;
  snapshot: ConversationSnapshot;
  changes?: ChangeSetView;
  review?: ReviewView;
  rules?: ProjectRules;
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
}

const tabs: Array<{ id: Tab; label: string; icon: typeof Files }> = [
  { id: "summary", label: "Summary", icon: SidebarSimple },
  { id: "changes", label: "Changes", icon: Files },
  { id: "terminal", label: "Terminal", icon: TerminalWindow },
  { id: "review", label: "Review", icon: ShieldCheck },
  { id: "rule", label: "Rule", icon: BookOpenText },
];

export function ToolCanvas({
  projectId,
  snapshot,
  changes,
  review,
  rules,
  busy,
  fileContent,
  onLoadFile,
  onAccept,
  onDiscard,
  onRequestChanges,
  onReviseRules,
  onCreateRule,
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
  const [ruleKind, setRuleKind] =
    useState<RuleDefinition["kind"]>("code_standard");
  const [ruleEnforcement, setRuleEnforcement] =
    useState<RuleDefinition["enforcement"]>("advisory");
  const currentCandidate =
    review?.candidate_id || snapshot.conversation.active_candidate_id || "";

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
        (item) => !["completed", "cancelled"].includes(item.status),
      ),
    [snapshot.assignments],
  );

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
                <h2>{labelStatus(snapshot.conversation.status)}</h2>
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
            <section className="panel-section">
              <h3>可见 Fleet</h3>
              {snapshot.assignments.map((assignment) => (
                <div className="summary-row" key={assignment.assignment_id}>
                  <span className={`state-dot ${assignment.status}`} />
                  <div>
                    <strong>{assignment.agent_id}</strong>
                    <small>{assignment.title}</small>
                  </div>
                  <span>{labelStatus(assignment.status)}</span>
                </div>
              ))}
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
                <h2>{review?.outcome ?? "正在形成评审视图"}</h2>
              </div>
            </header>
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
                <p className="muted">尚无人工交互、交付决策或修改请求。</p>
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
                <p className="muted">本轮尚无 Runtime 验证记录。</p>
              ) : null}
            </section>
            {review?.review_state === "pending" ? (
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
                    template:
                      ruleKind === "document_template"
                        ? normalizedInstructions
                        : null,
                  });
                  setRuleId("");
                  setRuleTitle("");
                  setRuleInstructions("");
                  setShowRuleForm(false);
                }}
              >
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
                    maxLength={12000}
                    onChange={(event) => setRuleInstructions(event.target.value)}
                    required
                  />
                </label>
                <small>
                  CI 门禁只能由已登记的 argv 验证命令创建，不接受网页任意 Shell。
                </small>
                <button className="primary" type="submit" disabled={busy}>
                  保存到个人 Rule 库
                </button>
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
                  <div>
                    <strong>{rule.title || rule.rule_id}</strong>
                    <small>
                      v{rule.version} · {rule.enforcement === "required" ? "强制" : "建议"}
                    </small>
                  </div>
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
                    <span>{frozen ? "已冻结" : binding ? "项目默认" : "全局"}</span>
                  </label>
                </header>
                <p>{rule.description || rule.instructions}</p>
                <footer>
                  <span>{rule.kind}</span>
                  <span>{rule.workflows.join(" / ") || "全部流程"}</span>
                </footer>
              </article>
              );
            })}
            {!rules?.library.length ? (
              <div className="tool-empty compact-empty">
                <BookOpenText />
                <strong>项目尚未绑定 Rule</strong>
                <p>可通过全局 Rule 库创建规范，再冻结到新的 Conversation。</p>
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
    </aside>
  );
}

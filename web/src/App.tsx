import {
  ArrowClockwise,
  ShieldCheck,
} from "@phosphor-icons/react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  acceptDelivery,
  ApiError,
  createConversation,
  createRule,
  discardDelivery,
  getChanges,
  getFile,
  getReview,
  getConversationSnapshot,
  getProjectRules,
  listAgents,
  listConversations,
  listProjects,
  registerProject,
  reviseConversationRules,
  requestChanges,
  sendMessage,
} from "./api";
import { NewConversationDialog } from "./components/NewConversationDialog";
import { ConversationRail } from "./components/ConversationRail";
import { ConversationTimeline } from "./components/ConversationTimeline";
import { ProjectRail } from "./components/ProjectRail";
import { ToolCanvas } from "./components/ToolCanvas";
import { useActivityStream } from "./hooks/useActivityStream";
import { labelStatus } from "./labels";

function formatError(error: unknown): string {
  if (error instanceof ApiError) {
    return [error.message, error.remediation].filter(Boolean).join("；");
  }
  return error instanceof Error ? error.message : String(error);
}

function projectFromPath(): string | null {
  const match = window.location.pathname.match(/^\/projects\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export default function App() {
  const queryClient = useQueryClient();
  const [projectId, setProjectId] = useState<string | null>(projectFromPath);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const newDialogReturnFocus = useRef<HTMLElement | null>(null);
  const [toast, setToast] = useState("");
  const [fileRequest, setFileRequest] = useState<{
    path: string;
    view: "baseline" | "current" | "diff";
  } | null>(null);

  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
    refetchInterval: 10_000,
  });
  useEffect(() => {
    const items = projects.data ?? [];
    if (!items.length) return;
    if (!projectId || !items.some((item) => item.project_id === projectId)) {
      setProjectId(items[0].project_id);
    }
  }, [projectId, projects.data]);

  useEffect(() => {
    if (!projectId) return;
    window.history.replaceState(null, "", `/projects/${encodeURIComponent(projectId)}`);
    const remembered = window.localStorage.getItem(`muxdev:last-conversation:${projectId}`);
    setSelectedId(remembered);
    setFileRequest(null);
  }, [projectId]);

  const conversations = useQuery({
    queryKey: ["projects", projectId, "conversations"],
    queryFn: () => listConversations(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: 10_000,
  });
  const agents = useQuery({
    queryKey: ["projects", projectId, "agents"],
    queryFn: () => listAgents(projectId!),
    enabled: Boolean(projectId),
  });
  const rules = useQuery({
    queryKey: ["projects", projectId, "rules"],
    queryFn: () => getProjectRules(projectId!),
    enabled: Boolean(projectId),
  });

  useEffect(() => {
    const list = conversations.data ?? [];
    if (!selectedId && list.length) setSelectedId(list[0].conversation_id);
    if (selectedId && list.length && !list.some((item) => item.conversation_id === selectedId)) {
      setSelectedId(list[0].conversation_id);
    }
  }, [conversations.data, selectedId]);

  useEffect(() => {
    if (projectId && selectedId) {
      window.localStorage.setItem(`muxdev:last-conversation:${projectId}`, selectedId);
    }
  }, [projectId, selectedId]);

  const snapshot = useQuery({
    queryKey: ["projects", projectId, "conversation", selectedId],
    queryFn: () => getConversationSnapshot(projectId!, selectedId!),
    enabled: Boolean(projectId && selectedId),
    staleTime: 5_000,
  });
  const activeRunId =
    typeof snapshot.data?.active_turn?.run_id === "string"
      ? snapshot.data.active_turn.run_id
      : snapshot.data?.conversation.active_run_id || undefined;
  const changes = useQuery({
    queryKey: ["projects", projectId, "changes", selectedId, activeRunId],
    queryFn: () => getChanges(projectId!, selectedId!, activeRunId),
    enabled: Boolean(projectId && selectedId && activeRunId),
  });
  const review = useQuery({
    queryKey: ["projects", projectId, "review", selectedId, activeRunId],
    queryFn: () => getReview(projectId!, selectedId!, activeRunId),
    enabled: Boolean(projectId && selectedId && activeRunId),
    retry: false,
  });
  const file = useQuery({
    queryKey: ["projects", projectId, "file", selectedId, activeRunId, fileRequest?.path, fileRequest?.view],
    queryFn: () =>
      getFile(projectId!, selectedId!, activeRunId!, fileRequest!.path, fileRequest!.view),
    enabled: Boolean(projectId && selectedId && activeRunId && fileRequest),
    retry: false,
  });

  useActivityStream(projectId, selectedId, queryClient);

  const refresh = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["projects"] }),
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "conversations"] }),
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "conversation", selectedId] }),
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "changes", selectedId] }),
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "review", selectedId] }),
    ]);
  }, [projectId, queryClient, selectedId]);

  const create = useMutation({
    mutationFn: (value: Parameters<typeof createConversation>[1]) =>
      createConversation(projectId!, value),
    onSuccess: async (result) => {
      setNewOpen(false);
      setSelectedId(result.conversation.conversation_id);
      const startupError = result.conversation.metadata?.startup_error as
        | { message?: string; remediation?: string }
        | undefined;
      setToast(
        startupError
          ? `Conversation 已创建，但 Agent 未启动：${startupError.message || "启动失败"}${
              startupError.remediation ? `；${startupError.remediation}` : ""
            }`
          : "任务已创建",
      );
      await refresh();
    },
    onError: (error) => setToast(`创建失败：${formatError(error)}`),
  });

  const send = useMutation({
    mutationFn: ({
      content,
      interactionId,
      recipients,
      dispatchKind,
    }: {
      content: string;
      interactionId?: string;
      recipients?: string[];
      dispatchKind?: string;
    }) =>
      sendMessage(
        projectId!,
        selectedId!,
        content,
        interactionId,
        recipients,
        dispatchKind,
      ),
    onSuccess: async (_result, variables) => {
      setToast(variables.interactionId ? "回答已提交" : "消息已发送");
      await refresh();
    },
    onError: (error) => setToast(`发送失败：${formatError(error)}`),
  });

  const action = useMutation({
    mutationFn: async (kind: "accept" | "discard") => {
      const candidateId =
        review.data?.candidate_id ||
        snapshot.data?.conversation.active_candidate_id;
      if (!candidateId) throw new Error("当前没有可结算的交付候选");
      return kind === "accept"
        ? acceptDelivery(projectId!, candidateId)
        : discardDelivery(projectId!, candidateId);
    },
    onSuccess: async (_result, kind) => {
      setToast(kind === "accept" ? "本轮变更已接受" : "本轮变更已安全回退");
      await refresh();
    },
    onError: (error) => setToast(`操作失败：${formatError(error)}`),
  });

  const revise = useMutation({
    mutationFn: ({
      message,
      path,
      line,
      endLine,
    }: {
      message: string;
      path?: string;
      line?: number;
      endLine?: number;
    }) =>
      requestChanges(
        projectId!,
        selectedId!,
        message,
        path ? [{ path, line, end_line: endLine }] : [],
      ),
    onSuccess: async () => {
      setToast("修改意见已发送，Agent 将在同一 Session 继续");
      await refresh();
    },
    onError: (error) => setToast(`请求修改失败：${formatError(error)}`),
  });
  const reviseRules = useMutation({
    mutationFn: (ruleIds: string[]) =>
      reviseConversationRules(projectId!, selectedId!, ruleIds),
    onSuccess: async () => {
      setToast("Rule 新版本已冻结，相关旧验证已标记过期");
      await refresh();
    },
    onError: (error) => setToast(`Rule 更新失败：${formatError(error)}`),
  });
  const addRule = useMutation({
    mutationFn: createRule,
    onSuccess: async (rule) => {
      setToast(`Rule 已创建：${rule.title}`);
      await queryClient.invalidateQueries({
        queryKey: ["projects", projectId, "rules"],
      });
    },
    onError: (error) => setToast(`Rule 创建失败：${formatError(error)}`),
  });

  const conversationSnapshot = snapshot.data;
  const openNewDialog = useCallback(() => {
    newDialogReturnFocus.current = document.activeElement as HTMLElement | null;
    void queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents"] });
    setNewOpen(true);
  }, [projectId, queryClient]);
  const addProject = useMutation({
    mutationFn: registerProject,
    onSuccess: async (project) => {
      setProjectId(project.project_id);
      setToast(`已登记项目：${project.name}`);
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error) => setToast(`项目登记失败：${formatError(error)}`),
  });
  return (
    <div className="app-shell">
      <ProjectRail
        projects={projects.data ?? []}
        selectedId={projectId}
        busy={addProject.isPending}
        onSelect={setProjectId}
        onAdd={async (path) => {
          await addProject.mutateAsync(path);
        }}
      />
      <ConversationRail
        conversations={conversations.data ?? []}
        selectedId={selectedId}
        onSelect={(id) => {
          setSelectedId(id);
          setFileRequest(null);
        }}
        onCreate={openNewDialog}
      />
      {conversationSnapshot ? (
        <section className="conversation-shell">
          <header className="conversation-header">
            <div className="conversation-heading">
              <div>
                <h1>{conversationSnapshot.conversation.title}</h1>
                <p>
                  {conversationSnapshot.conversation.primary_agent_id || "Agent"}
                  {" · "}
                  {conversationSnapshot.conversation.mode === "orchestrated"
                    ? "多 Agent 编排"
                    : "单 Agent"}
                  {" · "}
                  Run {String(activeRunId || "尚未开始").slice(-8)}
                </p>
              </div>
              <span
                className={`conversation-status ${conversationSnapshot.attention}`}
              >
                {labelStatus(conversationSnapshot.conversation.status)}
              </span>
            </div>
            <div className="header-actions">
              <button className="icon-button" type="button" aria-label="刷新" onClick={() => void refresh()}>
                <ArrowClockwise />
              </button>
            </div>
          </header>
          <div className="conversation-workspace">
            <ConversationTimeline
              snapshot={conversationSnapshot}
              sending={send.isPending}
              onSend={async (content, interactionId, recipients, dispatchKind) => {
                await send.mutateAsync({
                  content,
                  interactionId,
                  recipients,
                  dispatchKind,
                });
              }}
            />
            <ToolCanvas
              projectId={projectId!}
              snapshot={conversationSnapshot}
              changes={changes.data}
              review={review.data}
              rules={rules.data}
              busy={action.isPending || revise.isPending || reviseRules.isPending}
              fileContent={file.data}
              onLoadFile={(path, view) => setFileRequest({ path, view })}
              onAccept={() => action.mutate("accept")}
              onDiscard={() => action.mutate("discard")}
              onRequestChanges={(message, path, line, endLine) =>
                revise.mutateAsync({ message, path, line, endLine }).then(() => undefined)
              }
              onReviseRules={(ruleIds) =>
                reviseRules.mutateAsync(ruleIds).then(() => undefined)
              }
              onCreateRule={(rule) =>
                addRule.mutateAsync(rule).then(() => undefined)
              }
            />
          </div>
        </section>
      ) : selectedId && snapshot.isPending ? (
        <main className="welcome-state" aria-busy="true" aria-live="polite">
          <ArrowClockwise className="spin" />
          <h1>正在加载 Conversation</h1>
          <p>正在恢复活动账本、文件变化与评审上下文…</p>
        </main>
      ) : (
        <main className="welcome-state">
          <ShieldCheck />
          <h1>把一次聊天变成可验收的交付</h1>
          <p>
            创建 Conversation 后，活动、文件变化、验证和人工决策会持续保留。
          </p>
          <button className="primary" type="button" onClick={openNewDialog}>
            创建第一个任务
          </button>
        </main>
      )}
      <NewConversationDialog
        open={newOpen}
        agents={(agents.data ?? []).filter((item) => item.enabled)}
        rules={rules.data?.library ?? []}
        busy={create.isPending}
        returnFocus={newDialogReturnFocus.current}
        onClose={() => setNewOpen(false)}
        onSubmit={(value) => create.mutate(value)}
      />
      {toast ? (
        <div
          className={`toast ${toast.includes("失败") ? "error" : ""}`}
          role={toast.includes("失败") ? "alert" : "status"}
          onAnimationEnd={() => setToast("")}
        >
          {toast}
        </div>
      ) : null}
    </div>
  );
}

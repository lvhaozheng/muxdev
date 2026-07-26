import { ArrowsClockwise, HandPalm, Plug, Stop, TerminalWindow } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { restartSession } from "../api";
import { labelStatus } from "../labels";
import type { AgentSession } from "../types";

export function TerminalPanel({
  projectId,
  sessions,
}: {
  projectId: string;
  sessions: AgentSession[];
}) {
  const visibleSessions = useMemo(() => {
    const byAgent = new Map<string, AgentSession>();
    const statusRank = (status: string) =>
      ["starting", "ready", "busy", "waiting_input"].includes(status)
        ? 3
        : status === "resumable"
          ? 2
          : status === "failed"
            ? 1
            : 0;
    for (const session of sessions) {
      const current = byAgent.get(session.agent_id);
      if (
        !current ||
        statusRank(session.status) > statusRank(current.status) ||
        (statusRank(session.status) === statusRank(current.status) &&
          session.generation > current.generation)
      ) {
        byAgent.set(session.agent_id, session);
      }
    }
    return Array.from(byAgent.values());
  }, [sessions]);
  const [sessionId, setSessionId] = useState(
    visibleSessions[0]?.session_id ?? "",
  );
  const [connected, setConnected] = useState(false);
  const [lease, setLease] = useState("未连接");
  const [failure, setFailure] = useState<string>("");
  const [restarting, setRestarting] = useState(false);
  const [writable, setWritable] = useState(false);
  const writableRef = useRef(false);
  const [sessionState, setSessionState] = useState<AgentSession | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const socket = useRef<WebSocket | null>(null);
  const terminal = useRef<Terminal | null>(null);
  const outputCursor = useRef(0);
  const heartbeat = useRef<number | null>(null);
  const resizeObserver = useRef<ResizeObserver | null>(null);
  const selected = visibleSessions.find((item) => item.session_id === sessionId);
  const currentSession = sessionState?.session_id === sessionId
    ? sessionState
    : selected;

  useEffect(() => {
    if (!visibleSessions.some((item) => item.session_id === sessionId)) {
      setSessionId(visibleSessions[0]?.session_id ?? "");
    }
  }, [sessionId, visibleSessions]);

  useEffect(() => {
    setSessionState(selected ?? null);
  }, [selected]);

  useEffect(() => {
    return () => {
      socket.current?.close();
      terminal.current?.dispose();
      if (heartbeat.current !== null) window.clearInterval(heartbeat.current);
      resizeObserver.current?.disconnect();
    };
  }, []);

  function disconnect() {
    if (heartbeat.current !== null) {
      window.clearInterval(heartbeat.current);
      heartbeat.current = null;
    }
    resizeObserver.current?.disconnect();
    resizeObserver.current = null;
    socket.current?.close();
    socket.current = null;
    terminal.current?.dispose();
    terminal.current = null;
    outputCursor.current = 0;
    if (host.current) host.current.replaceChildren();
    setConnected(false);
    setWritable(false);
    writableRef.current = false;
    setLease("未连接");
  }

  function connect(requestWrite = false) {
    if (!sessionId || !host.current) return;
    disconnect();
    const terminalCapabilities =
      currentSession?.metadata?.terminal as Record<string, unknown> | undefined;
    const supportsResize = Boolean(terminalCapabilities?.resize);
    const term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
      fontSize: 12,
      theme: {
        background: "#141412",
        foreground: "#f5f2ea",
        cursor: "#f5f2ea",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host.current);
    fit.fit();
    terminal.current = term;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${protocol}//${window.location.host}/api/v2/projects/${encodeURIComponent(
        projectId,
      )}/sessions/${encodeURIComponent(sessionId)}/terminal`,
    );
    socket.current = ws;
    ws.onopen = () => {
      setConnected(true);
      setLease("连接中");
      setFailure("");
      ws.send(
        JSON.stringify({
          type: "attach",
          after_seq: 0,
          replay_scope: "current_generation",
          request_write: requestWrite,
          cols: term.cols,
          rows: term.rows,
        }),
      );
    };
    ws.onmessage = (event) => {
      const frame = JSON.parse(String(event.data)) as Record<string, unknown>;
      if (frame.type === "output") {
        outputCursor.current = Math.max(
          outputCursor.current,
          Number(frame.seq ?? 0),
        );
        term.write(String(frame.data ?? ""));
      }
      if (frame.type === "lease") {
        const granted = Boolean(frame.granted);
        setWritable(granted);
        writableRef.current = granted;
        setLease(granted ? "可写" : "只读");
        if (granted && heartbeat.current === null) {
          heartbeat.current = window.setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: "heartbeat" }));
            }
          }, 20_000);
        }
      }
      if (frame.type === "status") {
        const session = frame.session as AgentSession | undefined;
        if (session) setSessionState(session);
        if (session?.failure) {
          setFailure(session.failure.message);
          setLease("失败输出（只读）");
        } else if (session?.attached) {
          setLease((current) => (current === "连接中" ? "已连接" : current));
        }
      }
      if (frame.type === "error") {
        const message = String(frame.message ?? frame.code ?? "terminal error");
        const remediation = String(frame.remediation ?? "");
        setFailure([message, remediation].filter(Boolean).join("；"));
        term.writeln(
          `\r\n[muxdev] ${message}${remediation ? `\r\n[muxdev] ${remediation}` : ""}`,
        );
      }
    };
    ws.onclose = () => {
      setConnected(false);
      setLease("已断开");
      if (heartbeat.current !== null) {
        window.clearInterval(heartbeat.current);
        heartbeat.current = null;
      }
    };
    let inputBuffer = "";
    let inputFrame: number | null = null;
    const flushInput = () => {
      inputFrame = null;
      if (ws.readyState !== WebSocket.OPEN || !writableRef.current) {
        inputBuffer = "";
        return;
      }
      const pending = inputBuffer;
      inputBuffer = "";
      // 16K UTF-16 code units stay below the 64 KiB UTF-8 input-frame cap.
      for (let offset = 0; offset < pending.length; offset += 16_000) {
        ws.send(
          JSON.stringify({
            type: "input",
            data: pending.slice(offset, offset + 16_000),
          }),
        );
      }
    };
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN && writableRef.current) {
        inputBuffer += data;
        if (inputFrame === null) {
          inputFrame = window.requestAnimationFrame(flushInput);
        }
      }
    });
    let resizeTimer: number | null = null;
    let lastDimensions = "";
    const resize = new ResizeObserver(() => {
      fit.fit();
      if (!supportsResize) return;
      if (resizeTimer !== null) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        resizeTimer = null;
        const dimensions = `${term.cols}x${term.rows}`;
        if (ws.readyState !== WebSocket.OPEN || dimensions === lastDimensions) return;
        lastDimensions = dimensions;
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }, 100);
    });
    resizeObserver.current = resize;
    resize.observe(host.current);
    ws.addEventListener(
      "close",
      () => {
        resize.disconnect();
        if (inputFrame !== null) window.cancelAnimationFrame(inputFrame);
        if (resizeTimer !== null) window.clearTimeout(resizeTimer);
      },
      { once: true },
    );
  }

  async function restart() {
    if (!sessionId || restarting) return;
    setRestarting(true);
    setFailure("");
    try {
      const result = await restartSession(projectId, sessionId);
      const restartedSession = result.session as AgentSession | undefined;
      if (restartedSession) setSessionState(restartedSession);
      connect(false);
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      setRestarting(false);
    }
  }

  if (!visibleSessions.length) {
    return (
      <div className="tool-empty">
        <TerminalWindow />
        <strong>还没有可连接的 Session</strong>
        <p>Agent 启动后，逻辑终端会在这里出现。</p>
      </div>
    );
  }

  return (
    <section className="terminal-panel" aria-label="多 Agent Web 终端">
      <div className="terminal-picker">
        <select value={sessionId} onChange={(event) => setSessionId(event.target.value)}>
          {visibleSessions.map((session) => (
            <option key={session.session_id} value={session.session_id}>
              {session.agent_id} · G
              {session.session_id === sessionId && sessionState
                ? sessionState.generation
                : session.generation}{" "}
              ·{" "}
              {labelStatus(
                session.session_id === sessionId && sessionState
                  ? sessionState.status
                  : session.status,
              )}
            </option>
          ))}
        </select>
        <button
          className="secondary compact"
          type="button"
          onClick={() => connect()}
        >
          {connected ? <ArrowsClockwise /> : <Plug />}
          {connected
            ? "重新连接"
            : currentSession?.status === "failed"
              ? "只读查看失败输出"
              : currentSession?.status === "resumable"
                ? "恢复并打开"
                : "打开终端"}
        </button>
        {connected && currentSession?.status !== "failed" ? (
          <button
            className="secondary compact"
            type="button"
            onClick={() => {
              const ws = socket.current;
              const term = terminal.current;
              if (!ws || !term || ws.readyState !== WebSocket.OPEN) return;
              ws.send(
                JSON.stringify({
                  type: writable ? "release_write" : "attach",
                  after_seq: outputCursor.current,
                  replay_scope: "current_generation",
                  request_write: !writable,
                  cols: term.cols,
                  rows: term.rows,
                }),
              );
            }}
          >
            <HandPalm />
            {writable ? "释放控制权" : "获取控制权"}
          </button>
        ) : null}
        {connected && currentSession?.can_interrupt ? (
          <button
            className="secondary compact danger-action"
            type="button"
            onClick={() =>
              socket.current?.send(JSON.stringify({ type: "interrupt" }))
            }
          >
            <Stop weight="fill" />
            中断命令
          </button>
        ) : null}
        {currentSession?.status === "failed" ? (
          <button
            className="secondary compact"
            type="button"
            onClick={() => void restart()}
            disabled={restarting}
          >
            <ArrowsClockwise />
            {restarting ? "正在重新启动…" : "重新启动"}
          </button>
        ) : null}
      </div>
      <div className="terminal-status" id="terminal-lease-status" role="status">
        <span className={connected ? "live-dot" : "offline-dot"} aria-hidden="true" />
        {lease}
      </div>
      {failure || currentSession?.failure?.message ? (
        <div className="terminal-failure" role="alert">
          <strong>{failure || currentSession?.failure?.message}</strong>
          <p>
            {currentSession?.failure?.remediation ||
              "检查 Agent Doctor 和终端能力后重试。"}
          </p>
        </div>
      ) : null}
      <div className="terminal-host" ref={host} />
    </section>
  );
}

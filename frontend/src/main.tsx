import "./compat";
import "./focus";
import React, { useState, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowLeft,
  ArrowUp,
  ArrowDown,
  Plus,
  MagnifyingGlass,
  Gear,
  ClockCounterClockwise,
  NotePencil,
  ArrowsOut,
  Copy,
  Robot,
  ShieldCheck,
  CaretDown,
  Stop,
  Database,
  ChartBar,
  X,
  FolderOpen,
  MapPin,
  CheckCircle,
  WarningCircle,
} from "@phosphor-icons/react";
import { call, subscribe, requestId, RecordData as D } from "./bridge";
import { empty, snapshot, events, State } from "./state";
import "./style.css";
const terminal = new Set([
  "succeeded",
  "completed",
  "failed",
  "cancelled",
  "interrupted",
  "skipped",
]);
const words: Record<string, string> = {
  queued: "等待处理",
  running: "执行中",
  succeeded: "已完成",
  completed: "已完成",
  failed: "未完成",
  cancelled: "已停止",
  interrupted: "已中断",
  skipped: "未执行",
  interpreting: "理解请求",
  validating: "校验数据与策略",
  waiting_choice: "等待你的选择",
  blocked: "需要处理",
  submitted: "已提交",
  reviewing: "独立审查",
  needs_review: "需要复核",
  pass: "通过（非工程验收）",
  fail: "未通过",
  insufficient_evidence: "证据不足",
  model_review: "分析证据",
  read_evidence: "读取证据",
  evidence: "整理证据",
  verify: "复核证据",
  repair_gate: "检查重算条件",
  model_response: "组织回复",
};
const label = (s: string) => words[s] || s || "处理中";
function IconButton({
  title,
  onClick,
  children,
  disabled = false,
}: {
  title: string;
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      className="icon"
      title={title}
      aria-label={title}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
function Text({ text }: { text: string }) {
  return (
    <div className="markdown">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={(u) => (/^https?:\/\//i.test(u) ? u : "")}
        components={{
          a: ({ href, children }) => (
            <a
              href={href}
              onClick={(e) => {
                e.preventDefault();
                if (href) void call("external", { url: href });
              }}
            >
              {children}
            </a>
          ),
          pre: ({ children }) => (
            <div className="code">
              <button
                onClick={(e) =>
                  void call("copy", {
                    text:
                      e.currentTarget.parentElement?.querySelector("pre")
                        ?.textContent || "",
                  })
                }
              >
                复制
              </button>
              <pre>{children}</pre>
            </div>
          ),
          table: ({ children }) => (
            <div className="table-scroll">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}
function Json({ value, title = "详细记录" }: { value: any; title?: string }) {
  return (
    <details className="json">
      <summary>{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
function Process({ w, activities }: { w?: D; activities: D[] }) {
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const current = activities[activities.length - 1];
  const status = w?.status || current?.status || "running";
  const busy =
    !terminal.has(status) &&
    status !== "waiting_choice" &&
    status !== "blocked";
  const secs = Math.max(
    0,
    Math.round(
      (busy ? now / 1000 : w?.updated || current?.time || now / 1000) -
        (w?.created || current?.time || now / 1000),
    ),
  );
  return (
    <div className="process">
      <button className="process-head" onClick={() => setOpen(!open)}>
        {status === "reviewing" ? <ShieldCheck /> : <Robot />}
        <span className={busy ? "pulse" : ""}>
          {label(status)}
          {busy && current?.label ? " · " + label(current.label) : ""}
        </span>
        <small>{secs > 0 ? `${secs} 秒` : ""}</small>
        <CaretDown className={open ? "rotate" : ""} />
      </button>
      {open && (
        <div className="process-body">
          {activities.map((a, i) => (
            <div key={i}>
              <span>{a.type === "review" ? <ShieldCheck /> : <Gear />}</span>
              <details>
                <summary>
                  {label(a.label)} · {label(a.status)}
                </summary>
                {(a.lines || []).map((v: string, j: number) => (
                  <div key={j}>{label(v)}</div>
                ))}
              </details>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
function App() {
  const [page, setPage] = useState("home"),
    [sessions, setSessions] = useState<D[]>([]),
    [sid, setSid] = useState(""),
    [state, setState] = useState<State>(empty),
    [health, setHealth] = useState<D>({}),
    [error, setError] = useState(""),
    [search, setSearch] = useState(false),
    [query, setQuery] = useState("");
  const [text, setText] = useState(""),
    [sending, setSending] = useState(false),
    [next, setNext] = useState<number | null>(null),
    [runs, setRuns] = useState<D[]>([]),
    [selected, setSelected] = useState<D | null>(null),
    [reviews, setReviews] = useState<D[]>([]),
    [report, setReport] = useState<any>(null),
    [compare, setCompare] = useState("");
  const [draft, setDraft] = useState<D>({
      revision: 0,
      sources: {},
      recognition_path: null,
    }),
    [dirty, setDirty] = useState(false),
    [saveStatus, setSaveStatus] = useState(""),
    [scope, setScope] = useState("full"),
    [params, setParams] = useState("{}"),
    [url, setUrl] = useState(""),
    [busy, setBusy] = useState(false),
    [down, setDown] = useState(false);
  const active = useRef(""),
    generation = useRef(0),
    draftRef = useRef(draft),
    dirtyRef = useRef(false),
    saving = useRef<Promise<void> | null>(null),
    scroll = useRef<HTMLDivElement>(null),
    bottom = useRef(true),
    pendingSend = useRef<{ text: string; id: string } | null>(null),
    pendingAction = useRef<{ key: string; id: string } | null>(null);
  const guarded = async (fn: () => Promise<any>) => {
    setError("");
    try {
      return await fn();
    } catch (e) {
      setError(String((e as Error).message));
      return undefined;
    }
  };
  const refresh = async () => {
    const [h, s] = await Promise.all([call("health"), call("sessions")]);
    setHealth(h);
    setSessions(s);
    if (h.ui_protocol !== "web-v1")
      throw new Error("后端尚不支持此界面，请更新并重启后端");
  };
  useEffect(() => {
    void guarded(async () => {
      const v = await call("bootstrap");
      setUrl(v.url);
      await refresh();
      if (v.session_id) await openSession(v.session_id);
    });
    return subscribe((e) => {
      if (e.sid !== active.current) return;
      if (e.type === "events") {
        setState((s) => events(s, e.items));
        if (
          e.items.some((v: D) =>
            ["run", "review", "workflow", "session_title", "check"].includes(
              v.type,
            ),
          )
        )
          void refreshRuns(e.sid);
        if (e.items.some((v: D) => v.type === "session_title"))
          void call("sessions").then(setSessions);
      }
      if (e.type === "connectionError") setError(e.message);
    });
  }, []);
  useEffect(() => {
    if (page !== "runs" || !sid) return;
    let live = true;
    const t = setInterval(() => {
      void call("runs", { sid })
        .then((r) => {
          if (live && active.current === sid) setRuns(r);
        })
        .catch(() => {});
      if (selected)
        void Promise.all([
          call("run", { sid, rid: selected.id }),
          call("reviews", { sid, rid: selected.id }),
        ])
          .then(([r, v]) => {
            if (live && active.current === sid) {
              setSelected(r);
              setReviews(v);
            }
          })
          .catch(() => {});
    }, 1500);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [page, sid, selected?.id]);
  useEffect(() => {
    if (bottom.current)
      scroll.current?.scrollTo(0, scroll.current.scrollHeight);
  }, [state]);
  useEffect(() => {
    if (!dirty) return;
    const t = setTimeout(() => void guarded(flush), 600);
    return () => clearTimeout(t);
  }, [draft, dirty]);
  async function ensureSession() {
    if (active.current) return active.current;
    const s = await call("createSession", { payload: {} });
    active.current = s.id;
    setSid(s.id);
    await call("subscribe", { sid: s.id, cursor: 0 });
    await call("sessions").then(setSessions);
    return s.id;
  }
  async function flush() {
    if (saving.current) {
      await saving.current;
      if (dirtyRef.current) await flush();
      return;
    }
    if (!dirtyRef.current) return;
    saving.current = (async () => {
      setSaveStatus("保存中…");
      const id = await ensureSession(),
        value = draftRef.current;
      const saved = await call("saveData", {
        sid: id,
        payload: {
          revision: value.revision,
          sources: Object.fromEntries(
            Object.entries(value.sources).filter(([, v]) =>
              (v as D).path?.trim(),
            ),
          ),
          recognition_path: value.recognition_path || null,
        },
      });
      if (active.current !== id) return;
      const changed = draftRef.current !== value;
      draftRef.current = changed
        ? { ...draftRef.current, revision: saved.revision }
        : saved;
      dirtyRef.current = changed;
      setDraft(draftRef.current);
      setDirty(changed);
      setSaveStatus(changed ? "等待保存" : "已保存，正在检查数据…");
      if (!changed) {
        try {
          const checked = await call("checkData", { sid: id, payload: {} });
          if (active.current === id && draftRef.current === saved)
            setSaveStatus(checked.valid ? "已保存并检查" : "已保存；数据检查需处理：" + (checked.error || "请在对话中查看检查结果"));
        } catch {
          if (active.current === id && draftRef.current === saved)
            setSaveStatus("已保存；自动检查未完成，请检查后端连接");
        }
      }
    })().finally(() => {
      saving.current = null;
    });
    await saving.current;
    if (dirtyRef.current) await flush();
  }
  function changeDraft(value: D) {
    draftRef.current = value;
    dirtyRef.current = true;
    setDraft(value);
    setDirty(true);
    setSaveStatus("等待保存");
  }
  async function openSession(id: string) {
    await flush();
    const g = ++generation.current;
    active.current = id;
    setSid(id);
    setPage("chat");
    setSearch(false);
    setSelected(null);
    setReport(null);
    setState(empty());
    bottom.current = true;
    const [h, d] = await Promise.all([
      call("history", { sid: id }),
      call("data", { sid: id }),
    ]);
    if (g !== generation.current) return;
    setState(snapshot(empty(), h));
    setNext(h.next_before);
    draftRef.current = d;
    setDraft(d);
    dirtyRef.current = false;
    setDirty(false);
    setSaveStatus("已保存");
    await call("subscribe", { sid: id, cursor: h.cursor });
    await refreshRuns(id);
  }
  async function home() {
    await flush();
    generation.current++;
    active.current = "";
    setSid("");
    setPage("home");
    setState(empty());
    setSelected(null);
    setText("");
    draftRef.current = { revision: 0, sources: {}, recognition_path: null };
    setDraft(draftRef.current);
    await call("unsubscribe");
    await refresh();
  }
  async function refreshRuns(id: string) {
    const r = await call("runs", { sid: id });
    if (active.current === id) setRuns(r);
  }
  async function send() {
    if (!text.trim() || sending) return;
    setSending(true);
    try {
      await flush();
      const id = await ensureSession();
      const body = text.trim();
      if (pendingSend.current?.text !== body)
        pendingSend.current = { text: body, id: requestId() };
      const m = await call("message", {
        sid: id,
        payload: { request_id: pendingSend.current.id, content: body },
      });
      pendingSend.current = null;
      if (active.current !== id) return;
      setText("");
      setPage("chat");
      setState((s) =>
        s.messages.some((v) => v.id === m.id)
          ? s
          : { ...s, messages: [...s.messages, m] },
      );
      bottom.current = true;
    } finally {
      setSending(false);
    }
  }
  async function inspectRun(rid: string) {
    const id = active.current,
      g = generation.current;
    const [r, v] = await Promise.all([
      call("run", { sid: id, rid }),
      call("reviews", { sid: id, rid }),
    ]);
    if (g !== generation.current) return;
    setSelected(r);
    setReviews(v);
    setPage("runs");
    setReport(null);
  }
  async function operation(
    action: string,
    payload: D = {},
    rid = selected?.id,
  ) {
    if (busy) return;
    setBusy(true);
    try {
      const id = active.current;
      const key = JSON.stringify([id, action, rid, payload]);
      if (pendingAction.current?.key !== key)
        pendingAction.current = { key, id: requestId() };
      const r = await call(action, {
        sid: id,
        rid,
        payload: {
          ...(["plan", "validate", "export", "review"].includes(action)
            ? { request_id: pendingAction.current.id }
            : {}),
          ...payload,
        },
      });
      pendingAction.current = null;
      if (active.current === id) {
        setReport(r);
        await refreshRuns(id);
      }
    } finally {
      setBusy(false);
    }
  }
  async function choose(w: D, oid: string) {
    if (busy) return;
    setBusy(true);
    try {
      const updated = await call("resolve", {
        sid,
        wid: w.id,
        payload: {
          request_id: requestId(),
          option_id: oid,
          version: w.version,
        },
      });
      if (active.current === sid)
        setState((s) => snapshot(s, { messages: [], workflows: [updated] }));
    } finally {
      setBusy(false);
    }
  }
  async function stop() {
    const workflows = Object.values(state.workflows).filter(
      (w) => !terminal.has(w.status),
    );
    for (const w of workflows)
      await call("cancelWorkflow", { sid, wid: w.id, payload: {} });
    for (const m of state.messages.filter(
      (m) => !m.notification && !terminal.has(m.status),
    ))
      await call("cancelTurn", { sid, mid: m.id, payload: {} });
  }
  const running =
    state.messages.some((m) => !m.notification && !terminal.has(m.status)) ||
    Object.values(state.workflows).some(
      (w) =>
        !terminal.has(w.status) &&
        !["waiting_choice", "blocked"].includes(w.status),
    );
  const title = sessions.find((s) => s.id === sid)?.title || "新对话";
  const userMessages = state.messages.filter((m) => !m.notification);
  return (
    <div className="app">
      <header>
        <div className="brand">FTTH</div>
        <IconButton
          title="扩大插件窗口"
          onClick={() => void guarded(() => call("expand"))}
        >
          <ArrowsOut />
        </IconButton>
      </header>
      <nav>
        <div className="nav-title">
          {page !== "home" && (
            <IconButton
              title="返回"
              onClick={() =>
                void guarded(
                  page === "chat"
                    ? home
                    : async () => setPage(sid ? "chat" : "home"),
                )
              }
            >
              <ArrowLeft />
            </IconButton>
          )}
          <span>
            {page === "home"
              ? "Chats"
              : page === "data"
                ? "项目数据"
                : page === "runs"
                  ? "运行结果"
                  : page === "settings"
                    ? "设置"
                    : title}
          </span>
        </div>
        <div className="tools">
          <IconButton title="历史会话" onClick={() => setSearch(!search)}>
            <ClockCounterClockwise />
          </IconButton>
          <IconButton title="项目数据" onClick={() => setPage("data")}>
            <Database />
          </IconButton>
          <IconButton title="运行结果" onClick={() => setPage("runs")}>
            <ChartBar />
          </IconButton>
          <IconButton title="设置" onClick={() => setPage("settings")}>
            <Gear />
          </IconButton>
          <IconButton title="新对话" onClick={() => void guarded(home)}>
            <NotePencil />
          </IconButton>
        </div>
      </nav>
      {search && (
        <div className="history popover">
          <label>
            <MagnifyingGlass />
            <input
              autoFocus
              placeholder="搜索最近会话"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <IconButton title="关闭" onClick={() => setSearch(false)}>
              <X />
            </IconButton>
          </label>
          <div>
            {sessions
              .filter((s) => (s.title || "").includes(query))
              .map((s) => (
                <button
                  key={s.id}
                  onClick={() => void guarded(() => openSession(s.id))}
                >
                  {s.title || "新对话"}
                  <small>
                    {new Date(
                      (s.updated || s.created) * 1000,
                    ).toLocaleDateString()}
                  </small>
                </button>
              ))}
          </div>
        </div>
      )}
      {error && (
        <div className="error" role="alert">
          <WarningCircle />
          <span>{error}</span>
          <IconButton title="关闭提示" onClick={() => setError("")}>
            <X />
          </IconButton>
        </div>
      )}
      <main
        ref={scroll}
        onScroll={() => {
          const el = scroll.current!;
          bottom.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          setDown(!bottom.current);
        }}
      >
        {page === "home" && (
          <>
            <div className="recent">
              {sessions.slice(0, 3).map((s) => (
                <button
                  key={s.id}
                  onClick={() => void guarded(() => openSession(s.id))}
                >
                  {s.title || "新对话"}
                  <small>
                    {new Date(
                      (s.updated || s.created) * 1000,
                    ).toLocaleDateString()}
                  </small>
                </button>
              ))}
              {sessions.length > 3 && (
                <button className="muted" onClick={() => setSearch(true)}>
                  查看全部（{sessions.length}）
                </button>
              )}
            </div>
            <div className="empty">
              <Robot size={42} weight="thin" />
            </div>
          </>
        )}
        {page === "chat" && (
          <div className="transcript">
            {next && (
              <button
                className="older"
                onClick={() =>
                  void guarded(async () => {
                    const p = await call("history", { sid, before: next });
                    if (active.current !== sid) return;
                    setState((s) => snapshot(s, p, true));
                    setNext(p.next_before);
                  })
                }
              >
                加载更早的对话
              </button>
            )}
            {state.messages.map((m) => {
              const w = Object.values(state.workflows).find(
                (w) => w.message_id === m.id,
              );
              const a = Object.values(state.activities).filter(
                (a) =>
                  a.message_id === m.id ||
                  (!!a.run_id &&
                    (a.run_id === w?.run_id ||
                      a.run_id === w?.active_run_id)) ||
                  (!!a.review_id && a.review_id === w?.review_id),
              );
              const run = runs.find(
                (r) => r.id === (m.run_id || w?.run_id || m.submitted_run_id),
              );
              const output = m.response || m.partial_response || "";
              const action = w?.intent?.action;
              const isPlan = action === "plan";
              return (
                <article key={m.id}>
                  {!m.notification && (
                    <div className="user-message">{m.content}</div>
                  )}
                  {isPlan && (
                    <p className="opening">
                      本次将进行
                      {w?.intent?.scope === "partition" ? "分区" : "完整"}
                      规划，随后独立审查。先核对数据与工程策略，再根据实际结果给出总结。
                    </p>
                  )}
                  {!m.notification &&
                    (w || a.length > 0 || !terminal.has(m.status)) && (
                      <Process w={w} activities={a} />
                    )}
                  {w?.status === "waiting_choice" && (
                    <div className="choice">
                      <p>{w.detail}</p>
                      {w.options?.map((o: D) => (
                        <button
                          key={o.id}
                          disabled={busy}
                          onClick={() => void guarded(() => choose(w, o.id))}
                        >
                          {o.label}
                        </button>
                      ))}
                    </div>
                  )}
                  {output &&
                    !(
                      w?.status === "waiting_choice" && output === w.detail
                    ) && (
                      <>
                        <Text text={output} />
                        <div className="message-actions">
                          <IconButton
                            title="复制回复"
                            onClick={() =>
                              void guarded(() => call("copy", { text: output }))
                            }
                          >
                            <Copy />
                          </IconButton>
                          {(m.run_id || w?.run_id || m.submitted_run_id) && (
                            <button
                              onClick={() =>
                                void guarded(() =>
                                  inspectRun(
                                    m.run_id || w?.run_id || m.submitted_run_id,
                                  ),
                                )
                              }
                            >
                              查看结果
                            </button>
                          )}
                          {run?.status === "succeeded" && (
                            <>
                              <button
                                disabled={busy}
                                onClick={() =>
                                  void guarded(() =>
                                    operation(
                                      "display",
                                      { run_id: run.id },
                                      run.id,
                                    ),
                                  )
                                }
                              >
                                定位地图
                              </button>
                              {run.scope === "full" && (
                                <button
                                  disabled={busy}
                                  onClick={() =>
                                    void guarded(() =>
                                      operation(
                                        "export",
                                        { run_id: run.id },
                                        run.id,
                                      ),
                                    )
                                  }
                                >
                                  导出
                                </button>
                              )}
                              <button
                                disabled={busy}
                                onClick={() =>
                                  void guarded(() =>
                                    operation("review", {}, run.id),
                                  )
                                }
                              >
                                补充审查
                              </button>
                            </>
                          )}
                        </div>
                      </>
                    )}
                  {w?.status === "blocked" && output !== w.detail && (
                    <p className="notice">{w.detail}</p>
                  )}
                </article>
              );
            })}
            {!userMessages.length && (
              <p className="muted">开始一个新的规划对话。</p>
            )}
          </div>
        )}
        {page === "data" && (
          <section>
            <h2>项目数据</h2>
            <p className="muted">
              选择数据源，或使用 QGIS 当前图层。修改会自动保存到本会话。
            </p>
            <div className="actions">
              <button
                onClick={() =>
                  void guarded(async () => {
                    const v = await call("importConfig");
                    if (v) changeDraft({ ...draftRef.current, ...v });
                  })
                }
              >
                <FolderOpen />
                导入配置
              </button>
              <span className="muted">{saveStatus}</span>
            </div>
            {["IMB", "SITE", "PTECH", "INFRASTRUCTURE"].map((name) => (
              <div className="field-group" key={name}>
                <label>
                  {name}
                  <span className="muted">
                    {
                      (
                        {
                          IMB: "需求楼栋",
                          SITE: "局站",
                          PTECH: "技术节点",
                          INFRASTRUCTURE: "正式设施",
                        } as D
                      )[name]
                    }
                  </span>
                </label>
                <input
                  placeholder="数据文件路径"
                  value={draft.sources[name]?.path || ""}
                  onChange={(e) =>
                    changeDraft({
                      ...draft,
                      sources: {
                        ...draft.sources,
                        [name]: {
                          ...draft.sources[name],
                          path: e.target.value,
                          source_layer: draft.sources[name]?.source_layer || "",
                        },
                      },
                    })
                  }
                />
                <div className="actions">
                  <input
                    placeholder="子图层名称（可选）"
                    value={draft.sources[name]?.source_layer || ""}
                    onChange={(e) =>
                      changeDraft({
                        ...draft,
                        sources: {
                          ...draft.sources,
                          [name]: {
                            ...draft.sources[name],
                            source_layer: e.target.value,
                          },
                        },
                      })
                    }
                  />
                  <button
                    onClick={() =>
                      void guarded(async () => {
                        const path = await call("chooseFile");
                        if (path)
                          changeDraft({
                            ...draftRef.current,
                            sources: {
                              ...draftRef.current.sources,
                              [name]: { path, source_layer: "" },
                            },
                          });
                      })
                    }
                  >
                    选择文件
                  </button>
                  <button
                    onClick={() =>
                      void guarded(async () => {
                        const v = await call("currentLayer", { name });
                        changeDraft({
                          ...draftRef.current,
                          sources: { ...draftRef.current.sources, [name]: v },
                        });
                      })
                    }
                  >
                    当前图层
                  </button>
                </div>
              </div>
            ))}
            <div className="field-group">
              <label>识别道路（可选）</label>
              <input
                value={draft.recognition_path || ""}
                onChange={(e) =>
                  changeDraft({ ...draft, recognition_path: e.target.value })
                }
                placeholder="识别成果 GeoPackage"
              />
              <div className="actions">
                <button onClick={() => void guarded(async () => {
                  const id = active.current;
                  const path = await call("chooseRoads");
                  if (path && active.current === id) changeDraft({...draftRef.current, recognition_path: path});
                })}><FolderOpen />导入道路 GPKG</button>
                <button onClick={() => changeDraft({...draftRef.current, recognition_path: null})}>清除</button>
              </div>
            </div>
            {error && (
              <button
                onClick={() =>
                  void guarded(async () => {
                    if (!sid) return;
                    const v = await call("data", { sid });
                    draftRef.current = v;
                    dirtyRef.current = false;
                    setDraft(v);
                    setDirty(false);
                    setSaveStatus("已重新加载服务端版本");
                  })
                }
              >
                重新加载服务端数据（放弃未保存修改）
              </button>
            )}
          </section>
        )}
        {page === "runs" && (
          <section>
            <h2>运行结果</h2>
            {!sid ? (
              <p className="muted">先创建会话并添加项目数据。</p>
            ) : (
              <>
                <select
                  value={selected?.id || ""}
                  onChange={(e) =>
                    void guarded(() => inspectRun(e.target.value))
                  }
                >
                  <option value="">选择运行记录</option>
                  {runs.map((r) => (
                    <option key={r.id} value={r.id}>
                      {label(r.status)} · {r.id}
                    </option>
                  ))}
                </select>
                {selected && (
                  <>
                    <div className="result-card">
                      <h3>{label(selected.status)}</h3>
                      <p>
                        程序校验：
                        {selected.result?.release_status ||
                          selected.result?.downstream?.release_status ||
                          "查看校验详情"}
                      </p>
                      <p>
                        独立审查：
                        {label(
                          reviews[reviews.length - 1]?.result?.verdict ||
                            reviews[reviews.length - 1]?.status ||
                            "未审查",
                        )}
                      </p>
                      <p className="muted">
                        本版本为只读审查，不自动重算。
                        {selected.repair_count
                          ? `历史重算记录：${selected.repair_count} 次`
                          : ""}
                      </p>
                      <div className="actions">
                        <button
                          disabled={selected.status !== "succeeded" || busy}
                          onClick={() =>
                            void guarded(() =>
                              operation("display", { run_id: selected.id }),
                            )
                          }
                        >
                          <MapPin />
                          展示地图
                        </button>
                        <button
                          disabled={
                            selected.status !== "succeeded" ||
                            selected.scope !== "full" ||
                            busy
                          }
                          onClick={() =>
                            void guarded(() =>
                              operation("export", { run_id: selected.id }),
                            )
                          }
                        >
                          导出成果
                        </button>
                        <button
                          disabled={busy || !terminal.has(selected.status)}
                          onClick={() =>
                            void guarded(() => operation("review"))
                          }
                        >
                          补充审查
                        </button>
                        <button
                          disabled={busy || terminal.has(selected.status)}
                          onClick={() =>
                            void guarded(async () => {
                              await call("cancelRun", {
                                sid,
                                rid: selected.id,
                                payload: {},
                              });
                              await inspectRun(selected.id);
                            })
                          }
                        >
                          取消运行
                        </button>
                      </div>
                    </div>
                    {reviews.map((r) => (
                      <div key={r.id} className="review">
                        <h3>
                          <ShieldCheck /> {label(r.result?.verdict || r.status)}
                        </h3>
                        <p>{r.result?.summary || r.error_message || ""}</p>
                        {r.result?.findings?.map((f: D, i: number) => (
                          <details key={i}>
                            <summary>
                              <span className={"severity " + f.severity}>
                                {
                                  (
                                    {
                                      error: "错误",
                                      warning: "警告",
                                      info: "说明",
                                    } as D
                                  )[f.severity]
                                }
                              </span>
                              {f.description}
                            </summary>
                            <p>{f.suggestion}</p>
                            <p className="muted">
                              证据：{f.evidence_ids?.join("、")}
                            </p>
                          </details>
                        ))}
                        <Json value={r} title="审查证据与修复判定" />
                      </div>
                    ))}
                    <Json value={selected} title="参数、校验与成果详情" />
                    <div className="actions">
                      <select
                        value={compare}
                        onChange={(e) => setCompare(e.target.value)}
                      >
                        <option value="">选择对比运行</option>
                        {runs
                          .filter((r) => r.id !== selected.id)
                          .map((r) => (
                            <option key={r.id} value={r.id}>
                              {r.id}
                            </option>
                          ))}
                      </select>
                      <button
                        disabled={!compare}
                        onClick={() =>
                          void guarded(async () => {
                            const r = await call("compare", {
                              sid,
                              left: selected.id,
                              right: compare,
                            });
                            if (active.current === sid) setReport(r);
                          })
                        }
                      >
                        比较
                      </button>
                    </div>
                  </>
                )}
                <details>
                  <summary>手动规划与高级参数</summary>
                  <select
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                  >
                    <option value="full">完整规划</option>
                    <option value="partition">分区规划</option>
                  </select>
                  <p className="muted">
                    默认沿用算法默认参数与已保存工程策略。明确需要覆盖时填写参数
                    JSON。
                  </p>
                  <textarea
                    rows={5}
                    value={params}
                    onChange={(e) => setParams(e.target.value)}
                  />
                  <div className="actions">
                    <button
                      disabled={busy}
                      onClick={() =>
                        void guarded(async () => {
                          await flush();
                          await operation("validate", {
                            scope,
                            parameter_overrides: JSON.parse(params),
                          });
                        })
                      }
                    >
                      校验
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void guarded(async () => {
                          await flush();
                          await operation("plan", {
                            scope,
                            parameter_overrides: JSON.parse(params),
                          });
                        })
                      }
                    >
                      提交规划
                    </button>
                  </div>
                </details>
                {report && <Json value={report} title="本次操作结果" />}
              </>
            )}
          </section>
        )}
        {page === "settings" && (
          <section>
            <h2>设置</h2>
            <label>后端地址</label>
            <input value={url} onChange={(e) => setUrl(e.target.value)} />
            <div className="actions">
              <button
                onClick={() =>
                  void guarded(async () => {
                    await call("connection", { url });
                    await refresh();
                  })
                }
              >
                保存并连接
              </button>
              <button
                onClick={() =>
                  void guarded(async () => {
                    await call("chooseToken");
                    await refresh();
                  })
                }
              >
                选择连接令牌文件
              </button>
            </div>
            <h3>连接诊断</h3>
            <p>
              <CheckCircle /> 后端版本：{health.version || "未连接"}
            </p>
            <p>模型：{health.model_configured ? health.model : "未配置"}</p>
            <p>
              QGIS：
              {health.qgis?.version ||
                health.qgis?.qgis_version ||
                "查看诊断详情"}
            </p>
            <p className="muted">
              模型凭据由后端管理。连接令牌不会进入网页存储。
            </p>
            <button onClick={() => void guarded(refresh)}>重新检查</button>
            <Json value={health} />
          </section>
        )}
      </main>
      {down && page === "chat" && (
        <button
          className="to-bottom"
          aria-label="回到底部"
          onClick={() => {
            scroll.current?.scrollTo(0, scroll.current.scrollHeight);
            bottom.current = true;
            setDown(false);
          }}
        >
          <ArrowDown />
        </button>
      )}
      {["home", "chat"].includes(page) && (
        <footer>
          <div className="composer">
            <textarea
              placeholder={
                page === "home" ? "描述你的规划任务…" : "继续提问或提出修改…"
              }
              rows={2}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height =
                  Math.min(180, e.target.scrollHeight) + "px";
              }}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing &&
                  e.keyCode !== 229
                ) {
                  e.preventDefault();
                  void guarded(send);
                }
              }}
            />
            <div className="composer-tools">
              <IconButton title="添加项目数据" onClick={() => setPage("data")}>
                <Plus />
              </IconButton>
              <span className="muted">
                {dirty ? "数据待保存" : sid ? "当前会话" : "新对话"}
              </span>
              <span className="model">{health.model || "未配置模型"}</span>
              {running ? (
                <button
                  className="send"
                  title="停止本轮"
                  onClick={() => void guarded(stop)}
                >
                  <Stop weight="fill" />
                </button>
              ) : (
                <button
                  className="send"
                  title="发送"
                  disabled={!text.trim() || sending}
                  onClick={() => void guarded(send)}
                >
                  <ArrowUp weight="bold" />
                </button>
              )}
            </div>
          </div>
        </footer>
      )}
    </div>
  );

}
createRoot(document.getElementById("root")!).render(<App />);

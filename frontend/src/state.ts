import type { RecordData as D } from "./bridge";
export type State = {
  messages: D[];
  workflows: Record<string, D>;
  activities: Record<string, D>;
  seen: Set<number>;
};
export const empty = (): State => ({
  messages: [],
  workflows: {},
  activities: {},
  seen: new Set(),
});
export function snapshot(s: State, page: D, older = false): State {
  const merged = new Map(s.messages.map((m) => [m.id, m]));
  page.messages.forEach((m: D) => {
    if (!older || !merged.has(m.id)) merged.set(m.id, m);
  });
  const next = {
    ...s,
    messages: [...merged.values()].sort(
      (a, b) => (a.created || 0) - (b.created || 0),
    ),
    workflows: { ...s.workflows },
  };
  page.workflows.forEach((w: D) => {
    const old = next.workflows[w.id];
    if (!old || (w.revision || 0) >= (old.revision || 0))
      next.workflows[w.id] = w;
  });
  return events(next, page.activities || []);
}
export function events(s: State, items: D[]): State {
  const n: State = {
    messages: s.messages.map((m) => ({ ...m })),
    workflows: { ...s.workflows },
    activities: { ...s.activities },
    seen: new Set(s.seen),
  };
  for (const e of items) {
    if (n.seen.has(e.id)) continue;
    n.seen.add(e.id);
    if (e.type === "workflow") {
      const w = e.workflow,
        old = n.workflows[w.id];
      if (!old || (w.revision || 0) > (old.revision || 0))
        n.workflows[w.id] = w;
      continue;
    }
    if (["user", "assistant", "assistant_stream", "work"].includes(e.type)) {
      let m = n.messages.find((m) => m.id === e.message_id);
      if (!m) {
        m = {
          id: e.message_id,
          created: e.time,
          notification: e.type !== "user",
          status: "queued",
        };
        n.messages.push(m);
      }
      if (e.type === "user") {
        m.content = e.text;
        m.notification = false;
      }
      if (
        e.type === "assistant_stream" &&
        !["succeeded", "failed", "cancelled", "interrupted"].includes(m.status)
      ) {
        m.partial_response = e.text;
        m.status = "running";
      }
      if (e.type === "assistant") {
        m.response = e.text;
        m.status = e.status || "succeeded";
        m.run_id = e.run_id || m.run_id;
        m.review_id = e.review_id || m.review_id;
      }
      if (
        e.type === "work" &&
        !["succeeded", "failed", "cancelled", "interrupted"].includes(m.status)
      )
        m.status = e.status;
    }
    if (["work", "activity", "algorithm", "run", "review"].includes(e.type)) {
      const id = e.review_id || e.run_id || e.message_id;
      const old = n.activities[id];
      if (
        old &&
        [
          "succeeded",
          "completed",
          "failed",
          "cancelled",
          "interrupted",
          "skipped",
        ].includes(old.status) &&
        ["running", "queued"].includes(e.status)
      )
        continue;
      const label = e.label || e.name || e.summary?.stage || e.status;
      n.activities[id] = {
        ...old,
        ...e,
        label,
        lines: [...(old?.lines || []), label]
          .filter((v, i, a) => i === 0 || a[i - 1] !== v)
          .slice(-80),
      };
    }
  }
  return n;
}

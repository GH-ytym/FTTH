export type RecordData = Record<string, any>;
declare global {
  interface Window {
    qt?: { webChannelTransport: unknown };
    QWebChannel?: new (t: unknown, f: (c: any) => void) => unknown;
    __FTTH_TEST__?: boolean;
  }
}
type Host = {
  request: (s: string) => void;
  response: { connect: (f: (s: string) => void) => void };
  event: { connect: (f: (s: string) => void) => void };
};
let host: Host;
let sequence = 0;
const pending = new Map<
  string,
  {
    resolve: (v: any) => void;
    reject: (v: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  }
>();
const listeners = new Set<(e: RecordData) => void>();
export const ready = new Promise<void>((resolve, reject) => {
  if (!window.qt || !window.QWebChannel) {
    reject(new Error("请在 QGIS 插件中打开此界面"));
    return;
  }
  new window.QWebChannel(window.qt.webChannelTransport, (c) => {
    host = c.objects.host;
    host.response.connect((raw) => {
      const r = JSON.parse(raw),
        p = pending.get(r.id);
      if (!p) return;
      clearTimeout(p.timer);
      pending.delete(r.id);
      r.error ? p.reject(new Error(r.error)) : p.resolve(r.value);
    });
    host.event.connect((raw) => {
      const e = JSON.parse(raw);
      listeners.forEach((f) => f(e));
    });
    resolve();
  });
});
export async function call(
  action: string,
  args: RecordData = {},
): Promise<any> {
  await ready;
  const id = String(++sequence);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error("请求超时，请检查运行状态后重试"));
    }, 125000);
    pending.set(id, { resolve, reject, timer });
    host.request(JSON.stringify({ id, action, args }));
  });
}
export function subscribe(fn: (e: RecordData) => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}
export function requestId() {
  return (
    "web-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2)
  );
}

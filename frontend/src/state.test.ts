import { describe, it, expect } from "vitest";
import { empty, events, snapshot } from "./state";
describe("event replay", () => {
  it("deduplicates updates and rejects stale workflows", () => {
    const items = [
      { id: 1, type: "user", message_id: "m", text: "hello", time: 1 },
      {
        id: 2,
        type: "workflow",
        workflow: { id: "w", revision: 3, status: "running" },
      },
      {
        id: 3,
        type: "workflow",
        workflow: { id: "w", revision: 2, status: "waiting_choice" },
      },
    ];
    const s = events(events(empty(), items), items);
    expect(s.messages).toHaveLength(1);
    expect(s.workflows.w.status).toBe("running");
  });
  it("late stream cannot reopen completed reply", () => {
    const s = events(empty(), [
      {
        id: 1,
        type: "assistant",
        message_id: "m",
        text: "done",
        status: "succeeded",
      },
      { id: 2, type: "assistant_stream", message_id: "m", text: "draft" },
    ]);
    expect(s.messages[0].response).toBe("done");
    expect(s.messages[0].partial_response).toBeUndefined();
  });
  it("older history cannot replace current snapshots", () => {
    let s = snapshot(empty(), {
      messages: [{ id: "m", response: "new" }],
      workflows: [],
    });
    s = snapshot(
      s,
      { messages: [{ id: "m", response: "old" }], workflows: [] },
      true,
    );
    expect(s.messages[0].response).toBe("new");
  });
});

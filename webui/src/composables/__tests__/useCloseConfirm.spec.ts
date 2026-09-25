import { describe, expect, it } from "vitest";
import { useCloseConfirm } from "../useCloseConfirm";

describe("useCloseConfirm（045 v2 关闭确认编排）", () => {
  it("请求后打开确认框并记住场景", async () => {
    const flow = useCloseConfirm();
    const pending = flow.request("running_stop");
    expect(flow.open.value).toBe(true);
    expect(flow.scenario.value).toBe("running_stop");
    flow.cancel();
    await expect(pending).resolves.toBe("cancel");
  });

  it("取消后关闭确认框且不留下等待态", async () => {
    const flow = useCloseConfirm();
    const pending = flow.request("idle_save");
    flow.cancel();
    await pending;
    expect(flow.open.value).toBe(false);
    expect(flow.busy.value).toBe(false);
  });

  it("确认后进入等待态并保持打开，直到窗口关闭", async () => {
    const flow = useCloseConfirm();
    const pending = flow.request("idle_save");
    flow.confirm();
    expect(flow.busy.value).toBe(true);
    expect(flow.open.value).toBe(true);
    await expect(pending).resolves.toBe("confirm");
  });

  it("等待中重复请求不打断进行中的收尾", async () => {
    const flow = useCloseConfirm();
    const pending = flow.request("running_stop");
    flow.confirm();
    await expect(flow.request("idle_save")).resolves.toBe("cancel");
    await expect(pending).resolves.toBe("confirm");
  });

  it("等待中再次确认不改变结果", async () => {
    const flow = useCloseConfirm();
    const pending = flow.request("idle_save");
    flow.confirm();
    flow.confirm();
    await expect(pending).resolves.toBe("confirm");
  });

  it("没有待决请求时取消不报错", () => {
    const flow = useCloseConfirm();
    expect(() => flow.cancel()).not.toThrow();
    expect(flow.open.value).toBe(false);
  });
});

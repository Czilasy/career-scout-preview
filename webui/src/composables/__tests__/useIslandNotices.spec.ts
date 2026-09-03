import { describe, expect, it } from "vitest";
import { ref } from "vue";
import { createIslandNotices } from "../useIslandNotices";
import type { CapsuleStatusPayload } from "../useDiscoveryState";

function makeStatus(capsule: CapsuleStatusPayload["capsule"], overrides: Partial<CapsuleStatusPayload> = {}): CapsuleStatusPayload {
  return {
    platform: capsule.platform,
    phase: "judged",
    judged: 0,
    scope: capsule.platform,
    capsule,
    ...overrides,
  };
}

describe("useIslandNotices — 状态跃迁派生通知", () => {
  it("初始观察（prev=null）：不产生通知，避免复活已结束任务时弹幽灵", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 2 } }),
    );
    const { notices } = createIslandNotices(status);
    expect(notices.value).toHaveLength(0);
  });

  it("running → completed：派生 completed 通知（read:true，P2-1 裁决），detail 含匹配/待确认", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 10, total: 100 } }),
    );
    const api = createIslandNotices(status);
    expect(api.notices.value).toHaveLength(0);

    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 2 } });
    expect(api.notices.value).toHaveLength(1);
    expect(api.notices.value[0].kind).toBe("completed");
    expect(api.notices.value[0].title).toBe("本轮任务已完成");
    expect(api.notices.value[0].detail).toBe("匹配 5 · 待确认 2");
    expect(api.notices.value[0].target).toBe("results");
    // P2-1 裁决：完成信号已由 pill completed live state 展示，panel 行只是历史，
    // 不计未读——完成后点 pill 直达结果页（FR-008，等价被删 toast 一键直达）。
    expect(api.notices.value[0].read).toBe(true);
    expect(api.unreadCount.value).toBe(0);
  });

  it("scraped 阶段 detail 写作「待筛选 N」", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus(
        { state: "running", platform: "boss", progress: { phase: "scraping", done: 10, total: 100 } },
        { phase: "scraping" },
      ),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus(
      { state: "completed", platform: "boss", results: { matched: 42, pending: 0 } },
      { phase: "scraped" },
    );
    expect(api.notices.value).toHaveLength(1);
    expect(api.notices.value[0].detail).toBe("待筛选 42");
  });

  it("→attention/error：派生错误通知", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 5 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "网络断连" },
    });
    expect(api.notices.value).toHaveLength(1);
    expect(api.notices.value[0].kind).toBe("error");
    expect(api.notices.value[0].detail).toBe("网络断连");
  });

  it("→attention/paused：派生暂停通知", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 5 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "paused", message: "任务已暂停，请处理后继续" },
    });
    expect(api.notices.value[0].kind).toBe("paused");
  });

  it("多类并存：completed + error + paused 同时出现在池中", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 5 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 2 } });
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "抓取出错" },
    });
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "paused", message: "任务已暂停" },
    });
    expect(api.notices.value.map((n) => n.kind).sort()).toEqual(["completed", "error", "paused"]);
    // completed read:true（P2-1 裁决）→ 未读只算 error + paused。
    expect(api.unreadCount.value).toBe(2);
  });

  it("同 kind 替换：连续两次 →error 只产生一条 error 通知", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 1 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({ state: "attention", platform: "boss", attention: { kind: "error", message: "第一次错误" } });
    status.value = makeStatus({ state: "attention", platform: "boss", attention: { kind: "error", message: "第二次错误" } });
    expect(api.notices.value).toHaveLength(1);
    expect(api.notices.value[0].detail).toBe("第二次错误");
  });

  it("重复事件（内容不变）不把已读通知复活成未读", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 1 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "网络断连" },
    });
    api.markAllRead();
    expect(api.unreadCount.value).toBe(0);

    // SSE 重连等场景重发相同快照：内容没变，不得重新计未读。
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "网络断连" },
    });
    expect(api.unreadCount.value).toBe(0);
    expect(api.notices.value).toHaveLength(1);
  });

  it("scope=history（浏览历史轮）：completed 只展示不派生通知（复审 A3）", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 1 } }),
    );
    const api = createIslandNotices(status);
    // 切入历史轮 A：不得弹「本轮任务已完成」。
    status.value = makeStatus(
      { state: "completed", platform: "boss", results: { matched: 7, pending: 0 } },
      { scope: "history" },
    );
    expect(api.notices.value).toHaveLength(0);
    // 在历史轮 A/B 间切换：同样不得派发。
    status.value = makeStatus(
      { state: "completed", platform: "boss", results: { matched: 9, pending: 1 } },
      { scope: "history" },
    );
    expect(api.notices.value).toHaveLength(0);
    expect(api.unreadCount.value).toBe(0);
  });

  it("history 之后回到真实运行轮：running 清池后完成仍正常派生", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus(
        { state: "completed", platform: "boss", results: { matched: 7, pending: 0 } },
        { scope: "history" },
      ),
    );
    const api = createIslandNotices(status);
    expect(api.notices.value).toHaveLength(0);
    // 新一轮真实任务：running 不清池（038，但此时池本就空）→ completed 派生通知。
    status.value = makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 1, total: 20 } });
    expect(api.notices.value).toHaveLength(0);
    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 3, pending: 0 } });
    expect(api.notices.value).toHaveLength(1);
    expect(api.notices.value[0].detail).toBe("匹配 3");
  });

  it("首帧停在 history → 直接回 live completed（无 running）：不得弹幽灵通知（复审二 N1）", () => {
    // 启动恢复：首帧观察到的就是历史轮 completed（prev 不得被占位污染）。
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus(
        { state: "completed", platform: "boss", results: { matched: 7, pending: 0 } },
        { scope: "history" },
      ),
    );
    const api = createIslandNotices(status);
    expect(api.notices.value).toHaveLength(0);
    // 用户退出历史回到 live 已完成轮（historyRound 清空即直落 completed，无 running）：
    // prev 仍为 null → 走"初始观察不派生"，绝不弹"本轮任务已完成"。
    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 7, pending: 0 } });
    expect(api.notices.value).toHaveLength(0);
    expect(api.unreadCount.value).toBe(0);
  });

  it("038: 进入 running 不再清空通知池（终态历史保留，live state 由 carousel 派生）", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 1 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 2 } });
    expect(api.notices.value).toHaveLength(1);
    // 038：running 不再 clearAll — 终态通知保留在 panel 历史
    status.value = makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 0, total: 100 } });
    expect(api.notices.value).toHaveLength(1); // 仍保留
  });

  it("038: sinkInterrupt 把打断沉入 panel（kind=interrupt，未读；append 语义，P1-1）", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "idle", platform: "boss" }),
    );
    const api = createIslandNotices(status);
    expect(api.notices.value).toHaveLength(0);

    api.sinkInterrupt({
      id: "int-1",
      kind: "interrupt",
      title: "投递提醒",
      detail: "3条逾期",
      tone: "warning",
      target: "reminders",
    });
    expect(api.notices.value).toHaveLength(1);
    expect(api.notices.value[0].kind).toBe("interrupt");
    expect(api.notices.value[0].title).toBe("投递提醒");
    expect(api.notices.value[0].read).toBe(false);
    expect(api.unreadCount.value).toBe(1);

    // 连沉多条不同打断：append 逐条保留（复审 P1-1——upsert 按 kind 替换
    // 会互相吞掉，panel 只剩 1 条，US-3"panel 有 3 条未读"落空）。
    api.sinkInterrupt({
      id: "int-2",
      kind: "interrupt",
      title: "导出失败",
      detail: "",
      tone: "error",
      target: "task",
    });
    api.sinkInterrupt({
      id: "int-3",
      kind: "interrupt",
      title: "投递提醒",
      detail: "5条逾期",
      tone: "warning",
      target: "reminders",
    });
    expect(api.notices.value).toHaveLength(3);
    expect(api.unreadCount.value).toBe(3);
    expect(api.notices.value.map((n) => n.id)).toEqual(["int-1", "int-2", "int-3"]);
  });

  it("进入 idle 清空通知池（任务被重置）", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 0 } }),
    );
    // 上一轮（初始观察）不产通知；显式模拟 running → completed → idle。
    status.value = makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 1 } });
    const api = createIslandNotices(status);
    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 0 } });
    expect(api.notices.value).toHaveLength(1);
    status.value = makeStatus({ state: "idle", platform: "boss" });
    expect(api.notices.value).toHaveLength(0);
  });

  it("markRead / markAllRead：会话级已读，不影响通知存在", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 1 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "网络断连" },
    });
    // completed read:true（P2-1 裁决），未读从 error/paused/interrupt 起算。
    expect(api.unreadCount.value).toBe(1);
    const id = api.notices.value[0].id;
    api.markRead(id);
    expect(api.unreadCount.value).toBe(0);
    expect(api.notices.value[0].read).toBe(true);

    // 再触发一条内容更新的 error（新 id，重新计未读）
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "再次断连" },
    });
    expect(api.unreadCount.value).toBe(1);
    api.markAllRead();
    expect(api.unreadCount.value).toBe(0);
  });

  it("markReadBatch：只把集合内 id 标已读，其余保持未读（复审二 N2/三轮）", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 1 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 2 } });
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "网络断连" },
    });
    // completed read:true（P2-1）→ 未读只有 error 一条。
    expect(api.unreadCount.value).toBe(1);
    const errorId = api.notices.value.find((n) => n.kind === "error")!.id;

    // 不存在的 id：no-op；error 保持未读。
    api.markReadBatch(["ghost-id"]);
    expect(api.unreadCount.value).toBe(1);

    // markRead 单条 = batch 子集。
    api.markRead(errorId);
    expect(api.unreadCount.value).toBe(0);

    // 空数组 no-op。
    api.markReadBatch([]);
    expect(api.unreadCount.value).toBe(0);
  });

  it("markAllRead 后同 kind 内容更新仍重新计未读（新 id）", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 1 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "第一次错误" },
    });
    expect(api.unreadCount.value).toBe(1);
    api.markAllRead();
    expect(api.unreadCount.value).toBe(0);

    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "第二次错误" },
    });
    expect(api.unreadCount.value).toBe(1);
    expect(api.notices.value[0].detail).toBe("第二次错误");
  });

  it("reset()：清空通知 + 重置 prev（profile 切换语义）", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 1 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 2 } });
    expect(api.notices.value).toHaveLength(1);
    api.reset();
    expect(api.notices.value).toHaveLength(0);
    // reset 后即便直接给一个 completed 也不应产通知（prev=null 初始观察语义）
    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 9, pending: 0 } });
    expect(api.notices.value).toHaveLength(0);
  });
});
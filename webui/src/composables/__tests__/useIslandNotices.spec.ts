import { describe, expect, it } from "vitest";
import { ref, type Ref } from "vue";
import { createIslandNotices } from "../useIslandNotices";
import { useDiscoveryState } from "../useDiscoveryState";
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
    // 新一轮真实任务：running 不清池（037，但此时池本就空）→ completed 派生通知。
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

  it("037: 进入 running 不再清空通知池（终态历史保留，live state 由 carousel 派生）", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 1 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 2 } });
    expect(api.notices.value).toHaveLength(1);
    // 037：running 不再 clearAll — 终态通知保留在 panel 历史
    status.value = makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 0, total: 100 } });
    expect(api.notices.value).toHaveLength(1); // 仍保留
  });

  it("037: sinkInterrupt 把打断沉入 panel（kind=interrupt，未读；append 语义，P1-1）", () => {
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
    expect(status.value).toBeNull();
    // reset 后即便直接给一个 completed 也不应产通知（prev=null 初始观察语义）
    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 9, pending: 0 } });
    expect(api.notices.value).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// SPEC 046 第五轮：展开面板里那一行的标题。
// 真实浏览器现场——AI 筛选被服务重启打断（轨道 status=interrupted）时，同一屏的阶段卡
// 与顶栏胶囊都写「已中断」，唯独通知行的标题按 kind 硬映射成「任务已暂停」，
// 同一行里标题与 detail 两个说法。kind 只有一条暂停通道（去重、落点、未读都挂它），
// 性质必须由生产端一并传下来，行标题与胶囊共用同一份口径，不在此判第二次。
// ---------------------------------------------------------------------------
describe("useIslandNotices — 暂停族行标题与事实一致", () => {
  it("被打断的暂停通道：行标题说已中断，与 detail 不打架", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "screening", done: 5 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: {
        kind: "paused", pausedFact: "interrupted",
        message: "任务已中断，请处理后继续",
      },
    });
    const row = api.notices.value[0];
    // 行为语义不变：kind / 落点 / 未读照旧。
    expect(row.kind).toBe("paused");
    expect(row.target).toBe("attention");
    expect(api.unreadCount.value).toBe(1);
    expect(row.title).toContain("已中断");
    expect(row.title).not.toContain("已暂停");
    expect(row.detail).toContain("已中断");
    expect(row.detail).not.toContain("已暂停");
  });

  it("可恢复暂停：行标题仍说已暂停，不被中断口径带跑", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "screening", done: 5 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: {
        kind: "paused", pausedFact: "paused",
        message: "任务已暂停，请处理后继续",
      },
    });
    const row = api.notices.value[0];
    expect(row.kind).toBe("paused");
    expect(row.title).toContain("已暂停");
    expect(row.title).not.toContain("已中断");
    expect(row.detail).toContain("已暂停");
  });

  it("现场回归：胶囊判定为服务重启打断时，行标题跟着说已中断", () => {
    const state = useDiscoveryState({ profileId: "notice-paused-family" }, () => {});
    // 真实的服务重启现场：Flow 投影把这一轮报成未收尾（协调器投成 flowActive）。
    state.flowActive.value = true;
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "screening", done: 5 } }),
    );
    const api = createIslandNotices(status);

    state.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    const payload = state.roundStatusPayload.value;
    expect(payload?.capsule.state).toBe("attention");
    status.value = payload;

    const row = api.notices.value[0];
    expect(row.kind).toBe("paused");
    expect(row.title).toContain("已中断");
    expect(row.title).not.toContain("已暂停");
    expect(row.detail).toContain("已中断");
    expect(row.detail).not.toContain("已暂停");
  });

  // SPEC 046 D-09：已收尾轮次残留的中断快照不是轮次事实，不进通知池、不占展示位——
  // 岛按本轮真实收尾状态说话（池里的行仍按 D-06 逐行撤销，这里不涉及整池清空）。
  it("轮次已收尾的残留中断快照不产告警行", () => {
    const state = useDiscoveryState({ profileId: "notice-closed-round" }, () => {});
    state.flowActive.value = false;
    state.flowLiveWorker.value = false;
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "screening", done: 5 } }),
    );
    const api = createIslandNotices(status);

    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    const payload = state.roundStatusPayload.value;
    expect(payload?.capsule.state).not.toBe("attention");
    status.value = payload;

    expect(api.notices.value.some((row) => row.title.includes("已中断"))).toBe(false);
    expect(api.notices.value.some((row) => row.kind === "paused")).toBe(false);
  });

  it("现场回归：用户主动暂停的轮次，行标题仍说已暂停", () => {
    const state = useDiscoveryState({ profileId: "notice-paused-user" }, () => {});
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "screening", done: 5 } }),
    );
    const api = createIslandNotices(status);

    state.screenSnapshot.value = { status: "paused", progress: {}, logs: [] };
    const payload = state.roundStatusPayload.value;
    expect(payload?.capsule.state).toBe("attention");
    status.value = payload;

    const row = api.notices.value[0];
    expect(row.kind).toBe("paused");
    expect(row.title).toContain("已暂停");
    expect(row.title).not.toContain("已中断");
    expect(row.detail).toContain("已暂停");
  });
});

// ---------------------------------------------------------------------------
// SPEC 046 D-06：通知行必须带轮次归属，新一轮开始时上一轮的告警族通知失效。
// 真实现场——点「开始新一轮」并把中断轨道收尾为 cancelled 之后，灵动岛仍挂着
// 「任务已中断」那一行：running 分支故意不清池（037：用户可能还没看），而池里
// 的行又不认识"自己是哪一轮说的"，于是没有任何一条路径撤掉它。
// 修法只允许按轮次归属撤销上一轮的中断/attention 行；整池清掉会把本轮刚到达
// 的「结果已加入」一起吞掉（那是 US3 要求只提示一次的那一条）。
// 轮次身份取现场存档既有的轮次令牌（sceneStore.roundEpoch，开新一轮才换发），
// 本池不自己数轮次。
// ---------------------------------------------------------------------------
describe("useIslandNotices — 轮次归属与新一轮失效（D-06）", () => {
  /** 上一轮的现场：一条中断 attention 行 + 一条沉入的中断行 + 一条完成历史行。 */
  function previousRoundPool(round: Ref<string>) {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "screening", done: 5 } }),
    );
    const api = createIslandNotices(status, round);
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: {
        kind: "paused", pausedFact: "interrupted",
        message: "任务已中断，请开始新一轮",
      },
    });
    api.sinkInterrupt({
      id: "int-previous-round",
      kind: "interrupt",
      title: "上次 AI 筛选因服务重启被中断，已保留中断信息，可开始新一轮",
      detail: "",
      tone: "warning",
      target: "task",
    });
    return { api, status };
  }

  it("通知行登记到达时的轮次身份", () => {
    const round = ref("round-A");
    const { api } = previousRoundPool(round);
    expect(api.notices.value.every((notice) => notice.round === "round-A")).toBe(true);
  });

  it("开始新一轮：上一轮的中断行与 attention 行都撤销", () => {
    const round = ref("round-A");
    const { api } = previousRoundPool(round);
    expect(api.notices.value).toHaveLength(2);
    expect(api.unreadCount.value).toBe(2);

    round.value = "round-B";

    expect(api.notices.value).toHaveLength(0);
    expect(api.unreadCount.value).toBe(0);
  });

  it("撤销只按轮次归属：上一轮的完成历史行不属于告警族，不得被整池清掉", () => {
    const round = ref("round-A");
    const { api, status } = previousRoundPool(round);
    status.value = makeStatus({ state: "running", platform: "boss", progress: { phase: "screening", done: 8 } });
    status.value = makeStatus({ state: "completed", platform: "boss", results: { matched: 6, pending: 0 } });
    expect(api.notices.value.some((notice) => notice.kind === "completed")).toBe(true);

    round.value = "round-B";

    const completed = api.notices.value.filter((notice) => notice.kind === "completed");
    expect(completed).toHaveLength(1);
    expect(completed[0].detail).toBe("匹配 6");
    expect(api.notices.value.some((notice) => notice.kind === "paused")).toBe(false);
    expect(api.notices.value.some((notice) => notice.kind === "interrupt")).toBe(false);
  });

  it("正向配对：新一轮里刚到达的结果加入通知不被撤销", () => {
    const round = ref("round-A");
    const { api } = previousRoundPool(round);
    round.value = "round-B";

    api.pushNotice({
      id: "join-round-B",
      title: "BOSS 结果已加入",
      detail: "当前流程的新结果已原地合入",
      target: "results",
    });
    // 本轮再产一条通知：撤销只认轮次归属，不得顺手把池清掉。
    api.sinkInterrupt({
      id: "int-round-B",
      kind: "interrupt",
      title: "投递提醒",
      detail: "1条逾期",
      tone: "warning",
      target: "reminders",
    });

    expect(api.notices.value.map((notice) => notice.id)).toEqual(["join-round-B", "int-round-B"]);
    expect(api.notices.value.every((notice) => notice.round === "round-B")).toBe(true);
  });

  it("没有轮次身份时不误撤（未登记归属的行保持既有行为）", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "screening", done: 5 } }),
    );
    const api = createIslandNotices(status);
    status.value = makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "网络断连" },
    });
    const round = ref("");
    const wiredStatus = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "screening", done: 5 } }),
    );
    const wired = createIslandNotices(wiredStatus, round);
    wired.sinkInterrupt({
      id: "int-no-round", kind: "interrupt", title: "无轮次身份的行", detail: "", tone: "warning", target: "task",
    });

    expect(api.notices.value).toHaveLength(1);
    expect(wired.notices.value.map((notice) => notice.id)).toEqual(["int-no-round"]);
    round.value = "round-B";
    expect(wired.notices.value.map((notice) => notice.id)).toEqual(["int-no-round"]);
  });
});

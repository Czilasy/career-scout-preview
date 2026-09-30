import { flushPromises, mount } from "@vue/test-utils";
import TaskProgress from "../TaskProgress.vue";

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    status: "running",
    progress: { overall_percent: 10, current: 0, total: 10 },
    logs: [],
    ...overrides,
  };
}

describe("TaskProgress accessibility announcement", () => {
  it("does not announce percent-only changes", async () => {
    const wrapper = mount(TaskProgress, {
      props: { snapshot: snapshot() as never, kind: "screen" },
    });
    const announcement = () => wrapper.get('[data-testid="task-progress-announcement"]').text();
    const first = announcement();
    await wrapper.setProps({
      snapshot: snapshot({ progress: { overall_percent: 22, current: 0, total: 10 } }) as never,
    });
    expect(announcement()).toBe(first);
    wrapper.unmount();
  });

  it("announces stage and status changes once", async () => {
    const wrapper = mount(TaskProgress, {
      props: { snapshot: snapshot() as never, kind: "screen" },
    });
    const announcement = () => wrapper.get('[data-testid="task-progress-announcement"]').text();
    const first = announcement();

    await wrapper.setProps({
      snapshot: snapshot({ progress: { overall_percent: 22, stage: "ai_rough", current: 0, total: 10 } }) as never,
    });
    expect(announcement()).not.toBe(first);
    expect(announcement()).toContain("AI 粗筛");

    await wrapper.setProps({
      snapshot: snapshot({ status: "completed", progress: { overall_percent: 100 } }) as never,
    });
    expect(announcement()).toContain("已完成");
    wrapper.unmount();
  });

  // 并行页每张卡各挂一个 aria-live，播报没有主语时听者分不清是哪条线；
  // 播报必须自带平台名，同时继续排除百分比与用时，避免每秒刷屏。
  it("announces the platform the task belongs to, without percent or elapsed time", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          platform: "zhilian",
          progress: { overall_percent: 40, current: 4, total: 10, stage: "scrape" },
          started_at: Date.now() - 60_000,
          scraped_count: 40,
        }) as never,
        kind: "scrape",
      },
    });
    const announcement = wrapper.get('[data-testid="task-progress-announcement"]').text();
    expect(announcement).toContain("智联");
    expect(announcement).not.toContain("%");
    expect(announcement).not.toContain("秒");
    wrapper.unmount();
  });

  // 轨道快照没带平台时（读不到本段状态、走降级链），父组件下发的平台仍需进播报。
  it("announces the platform handed down by the parent when the snapshot omits it", () => {
    const wrapper = mount(TaskProgress, {
      props: { snapshot: snapshot({ platform: undefined }) as never, kind: "scrape", platform: "boss" },
    });
    expect(wrapper.get('[data-testid="task-progress-announcement"]').text()).toContain("BOSS");
    wrapper.unmount();
  });
});

describe("TaskProgress 用户结束保存口径", () => {
  it("用户主动结束保存的轮次不显示「任务因取消或停止而中断」", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "completed_with_pending",
          integrity: {
            conclusion: "interrupted",
            label: "已中断",
            primary_code: "interrupted",
            primary_reason: "任务因取消或停止而中断",
          },
        }) as never,
        kind: "screen",
        userFinished: true,
      },
    });

    expect(wrapper.text()).not.toContain("任务因取消或停止而中断");
    expect(wrapper.text()).toContain("已结束保存部分结果");
    wrapper.unmount();
  });
});

describe("TaskProgress diagnostics", () => {
  // 终态口径以后端 flow_tracks 白名单为唯一权威（done/succeeded/failed/stopped/
  // cancelled）。stopped 是真实写入的终态（store_flow_claims 停止轨道），
  // 之前漏在终态集合里：停止后仍转圈、仍显示「运行中」、计时继续跳动。
  it("treats a stopped Track as terminal: no spinner, 已停止, frozen timer", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "stopped",
          progress: { overall_percent: 42, current: 3, total: 7 },
          active_elapsed_ms: 5000,
        }) as never,
        kind: "scrape",
      },
    });

    expect(wrapper.get(".task-status").text()).toContain("已停止");
    expect(wrapper.find(".task-status .spin").exists()).toBe(false);
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 5秒");
    wrapper.unmount();
  });

  it("treats a succeeded Track as a completed terminal state", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "succeeded",
          progress: { overall_percent: 42, current: 3, total: 7 },
          active_elapsed_ms: 5000,
        }) as never,
        kind: "screen",
      },
    });

    expect(wrapper.get(".task-status").text()).toContain("已完成");
    expect(wrapper.find(".task-status .spin").exists()).toBe(false);
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 5秒");
    wrapper.unmount();
  });

  it("shows inline Chinese reason plus red error field for failed state", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "failed",
          error: "boom",
          pause_info: { error_code: "internal_error", error_reason: "boom" },
        }) as never,
        kind: "screen",
      },
    });
    expect(wrapper.get("[data-testid='pause-reason']").text()).toContain("boom · internal_error");
    expect(wrapper.get("[data-testid='error-field']").text()).toContain("internal_error");
    expect(wrapper.find("[data-testid='task-diagnostics']").exists()).toBe(false);
    expect(wrapper.find("[data-testid='copy-diagnostics']").exists()).toBe(false);
    wrapper.unmount();
  });

  it("shows inline reason plus code for paused state", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "paused",
          error: "captcha", pause_info: { error_code: "captcha_required", error_reason: "触发验证码" },
        }) as never,
        kind: "screen",
      },
    });
    expect(wrapper.get("[data-testid='pause-reason']").text()).toContain("触发验证码 · captcha_required");
    expect(wrapper.find("[data-testid='copy-diagnostics']").exists()).toBe(false);
    wrapper.unmount();
  });

  it("omits empty error field when no code is present", () => {
    const wrapper = mount(TaskProgress, {
      props: { snapshot: snapshot({ status: "failed", error: "boom" }) as never, kind: "screen" },
    });
    expect(wrapper.get("[data-testid='pause-reason']").text()).toBe("boom");
    expect(wrapper.find("[data-testid='error-field']").exists()).toBe(false);
    wrapper.unmount();
  });
});

describe("TaskProgress scrape counts", () => {
  it("paused + scrape 直接显示后端 current/total，不回退为 0/total", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: snapshot({
          status: "paused",
          stage: "scrape",
          total: 14,
          progress: { stage: "scrape", current: 4, total: 14, message: "已暂停" },
          pause_info: { error_code: "source_rate_limited", error_reason: "平台暂停" },
        }) as never,
      },
    });
    expect(wrapper.get('[data-testid="task-counts"]').text()).toContain("已完成 4 / 14");
    expect(wrapper.get('[data-testid="task-counts"]').text()).toContain("未开始 10");
    wrapper.unmount();
  });

  it("paused scrape 保持公共错误码的原始阻断原因", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: snapshot({
          status: "paused",
          stage: "scrape",
          total: 14,
          progress: { stage: "scrape", current: 4, total: 14, message: "已暂停" },
          pause_info: {
            error_code: "source_unreachable",
            error_reason: "系统性阻断：抓取脚本不可用",
          },
        }) as never,
      },
    });
    expect(wrapper.get('[data-testid="pause-reason"]').text()).toContain("系统性阻断：抓取脚本不可用");
    expect(wrapper.get('[data-testid="pause-reason"]').text()).not.toContain("平台暂时无法访问");
    wrapper.unmount();
  });

  it("paused scrape 无错误文案时不提示不存在的继续按钮", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: snapshot({
          status: "paused",
          stage: "scrape",
          total: 2,
          progress: { stage: "scrape", current: 1, total: 2, message: "已暂停" },
        }) as never,
      },
    });
    expect(wrapper.get('[data-testid="pause-reason"]').text()).toContain("任务已暂停，请处理后点继续");
    wrapper.unmount();
  });

  it.each(["boss", "zhilian"])("两平台对 source_unreachable 显示同一公共错误码文案：%s", (platform) => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: snapshot({
          status: "paused",
          platform,
          pause_info: { error_code: "source_unreachable" },
        }) as never,
      },
    });
    const reason = wrapper.get('[data-testid="pause-reason"]').text();
    expect(reason).toContain("抓取脚本不可用");
    expect(reason).toContain("source_unreachable");
    wrapper.unmount();
  });
});

describe("TaskProgress 033 V2 integrity", () => {
  const integrity = (conclusion: string, reason = "证据不足") => ({
    conclusion, label: conclusion, evidence_complete: conclusion === "succeeded",
    primary_reason: reason, recommendation: "建议重新执行", revision: 2,
  });

  it.each([
    ["succeeded", "完整成功"],
    ["empty", "已完成，没有找到岗位"],
    ["partial", "部分完成，部分结果可能缺失"],
    ["failed", "执行失败"],
    ["unverifiable", "无法确认是否完成"],
    ["interrupted", "任务已中断"],
  ])("uses the whitebox label for %s", (conclusion, label) => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "completed",
          integrity: integrity(conclusion, "主要原因"),
        }) as never,
        kind: "screen",
      },
    });
    expect(wrapper.get(".task-status").text()).toContain(label);
    expect(wrapper.get(".task-progress").attributes("data-integrity")).toBe(conclusion);
    if (["partial", "unverifiable"].includes(conclusion)) {
      expect(wrapper.get(".task-status").attributes("data-status")).not.toBe("completed");
    }
    wrapper.unmount();
  });

  // 轨道头部写「失败」、卡体写「完整成功」时，无障碍朗读会连成
  // 「智联失败完整成功」：显式状态必须压过完整性结论。
  it("显式失败与中断不被完整性结论盖掉", () => {
    const failed = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "failed", integrity: integrity("succeeded", "抓取证据完整"),
        }) as never,
        kind: "scrape",
      },
    });
    expect(failed.get(".task-status").text()).toContain("执行失败");
    expect(failed.get(".task-status").text()).not.toContain("完整成功");
    failed.unmount();

    const interrupted = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "interrupted", integrity: integrity("succeeded", "抓取证据完整"),
        }) as never,
        kind: "scrape",
      },
    });
    expect(interrupted.get(".task-status").text()).toContain("已中断");
    expect(interrupted.get(".task-status").text()).not.toContain("完整成功");
    interrupted.unmount();
  });

  it("无法确认时同时显示重新执行建议", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "completed",
          integrity: integrity("unverifiable", "缺少完成证据"),
        }) as never,
        kind: "screen",
      },
    });
    expect(wrapper.get('[data-testid="pause-reason"]').text()).toContain("建议重新执行");
    wrapper.unmount();
  });
});

describe("TaskProgress elapsed time: hours and pause-excluded duration", () => {
  afterEach(() => vi.useRealTimers());

  it("formats durations over one hour as X小时Y分Z秒", () => {
    // 57875s = 16h 4m 35s；旧实现会错误显示成 964分35秒。
    const finished = 1_000 + 57_875_000;
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "completed",
          progress: { stage: "done", overall_percent: 100 },
          started_at: 1_000,
          finished_at: finished,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 16小时4分35秒");
    wrapper.unmount();
  });

  it("shows the frozen cumulative elapsed while paused (paused time excluded)", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-01T00:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        // active_elapsed_ms=90s：实际运行 90 秒；started_at 到 now 为 10 分钟，
        // 说明 8.5 分钟处于暂停，暂停时长不得计入。
        snapshot: snapshot({
          status: "paused",
          progress: { stage: "fetch_jd", overall_percent: 40 },
          started_at: baseTime - 600_000,
          active_elapsed_ms: 90_000,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 1分30秒");
    // 暂停状态时间流逝不得回流到"已用"
    vi.advanceTimersByTime(30_000);
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 1分30秒");
    wrapper.unmount();
  });

  it("running shows cumulative + current segment via active_elapsed_ms", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-01T01:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "running",
          progress: { stage: "fetch_jd", overall_percent: 50 },
          started_at: baseTime - 600_000,
          active_elapsed_ms: 90_000,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分30秒");
    vi.advanceTimersByTime(2_000);
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分32秒");
    wrapper.unmount();
  });

  it("rebases active_elapsed_ms when a paused task resumes", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-01T02:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "paused",
          progress: { stage: "fetch_jd", overall_percent: 40 },
          started_at: baseTime - 600_000,
          active_elapsed_ms: 90_000,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 1分30秒");

    // 续跑：后端 active_elapsed_ms 从定格值继续增长（暂停的 5s 不计入）
    vi.setSystemTime(baseTime + 5_000);
    await wrapper.setProps({
      snapshot: snapshot({
        status: "running",
        progress: { stage: "fetch_jd", overall_percent: 45 },
        started_at: baseTime - 600_000,
        active_elapsed_ms: 95_000,
      }) as never,
    });
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分35秒");
    vi.advanceTimersByTime(2_000);
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分37秒");
    wrapper.unmount();
  });

  it("uses the fresh active_elapsed_ms baseline on each poll while running", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-08-01T03:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "running",
          progress: { stage: "fetch_jd", overall_percent: 50 },
          started_at: baseTime - 600_000,
          active_elapsed_ms: 90_000,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分30秒");

    // 下一轮轮询：后端按响应时刻重新计算，前端重置基准后不得跳变/重复计段
    vi.setSystemTime(baseTime + 2_000);
    await wrapper.setProps({
      snapshot: snapshot({
        status: "running",
        progress: { stage: "fetch_jd", overall_percent: 50 },
        started_at: baseTime - 600_000,
        active_elapsed_ms: 93_000,
      }) as never,
    });
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分33秒");
    vi.advanceTimersByTime(1_000);
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 1分34秒");
    wrapper.unmount();
  });

  // 046 第五轮真实复现：后端对 interrupted 的 active_elapsed_ms 已定格（连取两次差值为 0），
  // 但界面把「已中断」的卡演成还在跑（4.5 秒内「已用 18分57秒」走到「已用 19分01秒」）——
  // 叠加本地增量只能是真的有活 worker 在跑的状态。
  it("keeps an interrupted task's elapsed frozen while wall time advances", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-09-14T04:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        // started_at 距当前 20 分钟，其中 18分57秒 是真跑过的累计；定格值不得再被本地时钟拉长。
        snapshot: snapshot({
          status: "interrupted",
          progress: { stage: "screen_b", overall_percent: 62 },
          started_at: baseTime - 1_200_000,
          active_elapsed_ms: 1_137_000,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 18分57秒");
    vi.advanceTimersByTime(4_500);
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toContain("已用 18分57秒");
    wrapper.unmount();
  });

  // 排队（尚未开始）同样没有活 worker：只有后端定格值，不叠本地增量。
  it("keeps a queued task's elapsed on the backend value while wall time advances", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-09-14T06:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "queued",
          progress: { stage: "", overall_percent: 0 },
          started_at: baseTime,
          active_elapsed_ms: 0,
        }) as never,
      },
    });
    const first = wrapper.get(".task-elapsed").text();
    vi.advanceTimersByTime(5_000);
    await flushPromises();
    expect(wrapper.get(".task-elapsed").text()).toBe(first);
    wrapper.unmount();
  });

  // 反向回归：真有活 worker 在跑时计时必须继续走动。
  it("keeps advancing the elapsed label twice over while the task is really running", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
    const baseTime = new Date("2026-09-14T08:00:00.000Z").getTime();
    vi.setSystemTime(baseTime);
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "running",
          progress: { stage: "screen_b", overall_percent: 55 },
          started_at: baseTime - 600_000,
          active_elapsed_ms: 90_000,
        }) as never,
      },
    });
    const seconds = () => {
      const match = /已用 (?:(\d+)分)?(\d+)秒/.exec(wrapper.get(".task-elapsed").text());
      return match ? Number(match[1] || 0) * 60 + Number(match[2]) : Number.NaN;
    };
    const first = seconds();
    vi.advanceTimersByTime(3_000);
    await flushPromises();
    const second = seconds();
    expect(first).toBe(90);
    expect(second).toBe(first + 3);
    wrapper.unmount();
  });

  // 046 第五轮：任务快照没有任何计时证据（既无 started_at 也无 active_elapsed_ms）时，
  // 组件不得用本地 Date.now() 造一个起点、每秒走成「已用 X秒」。
  // 这类快照来自降级链（fallbackSnapshot），状态可为 unknown／interrupted／queued。
  // 门的口径与走活表判定同源：discovery.ts 的 elapsedRunsLive。
  for (const statusWithoutEvidence of ["unknown", "interrupted", "queued"]) {
    it(`不给没有计时证据的 ${statusWithoutEvidence} 卡造一个会走的钟`, async () => {
      vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
      const baseTime = new Date("2026-09-14T10:00:00.000Z").getTime();
      vi.setSystemTime(baseTime);
      const wrapper = mount(TaskProgress, {
        props: {
          kind: "scrape",
          snapshot: snapshot({ status: statusWithoutEvidence, progress: { overall_percent: 30 } }) as never,
        },
      });
      expect(wrapper.find(".task-elapsed").exists()).toBe(false);
      vi.advanceTimersByTime(5_000);
      await flushPromises();
      expect(wrapper.find(".task-elapsed").exists()).toBe(false);
      expect(wrapper.text()).not.toContain("已用");
      expect(wrapper.text()).not.toContain("用时");
      wrapper.unmount();
    });
  }

  // 反向保护：真的在跑、后端又没给任何计时字段时，本地回退钟仍是唯一的时间来源，必须走动。
  for (const liveStatus of ["running", "pausing"]) {
    it(`${liveStatus} 时后端没给计时字段，本地回退钟照样每秒走动`, async () => {
      vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "requestAnimationFrame", "performance"] });
      const baseTime = new Date("2026-09-14T12:00:00.000Z").getTime();
      vi.setSystemTime(baseTime);
      const wrapper = mount(TaskProgress, {
        props: {
          kind: "screen",
          snapshot: snapshot({ status: liveStatus, progress: { overall_percent: 55 } }) as never,
        },
      });
      const seconds = () => {
        const match = /已用 (?:(\d+)分)?(\d+)秒/.exec(wrapper.get(".task-elapsed").text());
        return match ? Number(match[1] || 0) * 60 + Number(match[2]) : Number.NaN;
      };
      const first = seconds();
      vi.advanceTimersByTime(3_000);
      await flushPromises();
      expect(first).toBe(0);
      expect(seconds()).toBe(first + 3);
      wrapper.unmount();
    });
  }

  it("falls back to started_at delta when active_elapsed_ms is absent", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "completed",
          progress: { stage: "done", overall_percent: 100 },
          started_at: 1_000,
          finished_at: 61_000,
        }) as never,
      },
    });
    expect(wrapper.get(".task-elapsed").text()).toContain("用时 1分00秒");
    wrapper.unmount();
  });
});

describe("TaskProgress 039 failure display", () => {
  const issue = (index: number, overrides: Record<string, unknown> = {}) => ({
    combo_key: `组合${index}`,
    code: "source_timeout",
    code_text: "抓取超时",
    reason: "第 9 页 30 秒无响应",
    ts: `ts-${index}`,
    ...overrides,
  });

  function mountWithIssues(issues: Array<Record<string, unknown>>, overrides = {}) {
    return mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: snapshot({
          status: "completed_with_pending",
          total: 16,
          success_count: 14,
          fail_count: issues.length,
          combo_issues: issues,
          progress: { stage: "done", overall_percent: 100 },
          ...overrides,
        }) as never,
      },
    });
  }

  it("失败只显示数量，不再成排展示明细，悬停前没有浮窗", () => {
    const wrapper = mountWithIssues([issue(0), issue(1)]);
    expect(wrapper.find('[data-testid="combo-issues"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="fail-count"]').text()).toBe("2");
    expect(wrapper.find('[data-testid="fail-tooltip"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("悬停失败数量逐条显示组合名与简单原因，移开后收起", async () => {
    const wrapper = mountWithIssues([issue(0), issue(1)]);
    await wrapper.get('[data-testid="fail-count-group"]').trigger("mouseenter");
    const tooltip = wrapper.get('[data-testid="fail-tooltip"]');
    expect(tooltip.text()).toContain("组合0：抓取超时");
    expect(tooltip.text()).toContain("组合1：抓取超时");
    // 不用“已跳过”“超时抓取”这类词代替原因
    expect(tooltip.text()).not.toContain("已跳过");
    expect(tooltip.text()).not.toContain("超时抓取");

    await wrapper.get('[data-testid="fail-count-group"]').trigger("mouseleave");
    expect(wrapper.find('[data-testid="fail-tooltip"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("浮窗最多 5 条，超出显示另有 N 条", async () => {
    const issues = Array.from({ length: 8 }, (_unused, index) => issue(index));
    const wrapper = mountWithIssues(issues, { fail_count: 8 });
    await wrapper.get('[data-testid="fail-count-group"]').trigger("mouseenter");
    const tooltip = wrapper.get('[data-testid="fail-tooltip"]');
    expect(tooltip.findAll(".fail-tooltip-row")).toHaveLength(5);
    expect(tooltip.text()).toContain("另有 3 条");
    wrapper.unmount();
  });

  it("原因名称缺失时回落用真实原因文本", async () => {
    const wrapper = mountWithIssues([
      { combo_key: "组合0", code: "source_unknown_error", code_text: "", reason: "页面解析异常", ts: "t" },
    ]);
    await wrapper.get('[data-testid="fail-count-group"]').trigger("mouseenter");
    expect(wrapper.get('[data-testid="fail-tooltip"]').text()).toContain("组合0：页面解析异常");
    wrapper.unmount();
  });

  it("空结果不是失败，不进失败浮窗", async () => {
    const wrapper = mountWithIssues([
      issue(0),
      { combo_key: "组合空", code: "combo_empty", code_text: "未搜到岗位", reason: "", ts: "empty" },
    ]);
    await wrapper.get('[data-testid="fail-count-group"]').trigger("mouseenter");
    const tooltip = wrapper.get('[data-testid="fail-tooltip"]').text();
    expect(tooltip).toContain("组合0：抓取超时");
    expect(tooltip).not.toContain("未搜到岗位");
    wrapper.unmount();
  });

  it("没有失败时不显示失败数字与浮窗入口", () => {
    const wrapper = mountWithIssues([], {
      status: "completed", fail_count: 0, combo_issues: [],
    });
    expect(wrapper.find('[data-testid="fail-count"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="fail-tooltip"]').exists()).toBe(false);
    wrapper.unmount();
  });
});

describe("TaskProgress 039 settled counts", () => {
  it("收尾后完成/失败/未开始与后端事实一致，不用矛盾默认值", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: snapshot({
          status: "completed_with_pending",
          total: 16,
          success_count: 14,
          fail_count: 2,
          // 旧接口可能留下错误的未开始数字，展示必须以后端完成/失败事实为准
          unstarted_count: 16,
          combo_issues: [
            { combo_key: "A|上海", code: "source_timeout", code_text: "抓取超时", reason: "", ts: "t1" },
            { combo_key: "B|上海", code: "source_timeout", code_text: "抓取超时", reason: "", ts: "t2" },
          ],
          progress: { stage: "done", overall_percent: 100 },
        }) as never,
      },
    });
    const counts = wrapper.get('[data-testid="task-counts"]').text();
    expect(counts).toContain("已完成 14 / 16");
    expect(counts).toContain("未开始 0");
    expect(wrapper.get('[data-testid="fail-count"]').text()).toBe("2");
    wrapper.unmount();
  });

  it("全部抓完且无失败时（收尾真实状态 completed）仍显示 16 / 16 与未开始 0", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: snapshot({
          status: "completed",
          total: 16,
          success_count: 16,
          fail_count: 0,
          progress: { stage: "done", overall_percent: 100 },
        }) as never,
      },
    });
    const counts = wrapper.get('[data-testid="task-counts"]').text();
    expect(counts).toContain("已完成 16 / 16");
    expect(counts).toContain("未开始 0");
    expect(wrapper.find('[data-testid="fail-count"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("AI 筛选任务（03 页）完整成功收尾也显示判定计数", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "completed",
          total: 2,
          success_count: 2,
          progress: { stage: "done", overall_percent: 100 },
        }) as never,
      },
    });
    expect(wrapper.get('[data-testid="task-counts"]').text()).toContain("已完成 2 / 2");
    expect(wrapper.get('[data-testid="task-counts"]').text()).toContain("未开始 0");
    wrapper.unmount();
  });

  it("分母未知时不显示 0/0 空数字，但已抓等真实数字不得整行收起", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "completed",
          total: 0,
          source_total: 143,
          scraped_count: 143,
          progress: { stage: "done", overall_percent: 100 },
        }) as never,
      },
    });
    // 已抓岗位是真实数字：计数行必须展示（用户拍板“永久展示”），
    // 只在分母未知（0）时不渲染「已完成 0 / 0」空数字。
    expect(wrapper.find('[data-testid="task-counts"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="scraped-count"]').text()).toContain("143");
    expect(wrapper.find(".count-current").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("已完成 0 / 0");
    wrapper.unmount();
  });

  it("纯抓取轮的筛选面板显示真实 0（已完成 0 / N、未开始 N），不整行隐藏", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "screen",
        snapshot: snapshot({
          status: "completed",
          total: 143,
          success_count: 0,
          fail_count: 0,
          unstarted_count: 143,
          source_total: 143,
          scraped_count: 143,
          progress: { stage: "done", overall_percent: 100 },
        }) as never,
      },
    });
    const counts = wrapper.get('[data-testid="task-counts"]').text();
    expect(counts).toContain("已完成 0 / 143");
    expect(counts).toContain("未开始 143");
    expect(wrapper.find('[data-testid="fail-count"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("运行中计数保持既有派生，不受收尾口径影响", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        kind: "scrape",
        snapshot: snapshot({
          status: "running",
          total: 14,
          success_count: 3,
          fail_count: 1,
          progress: { stage: "waiting", current: 4, total: 14 },
        }) as never,
      },
    });
    const counts = wrapper.get('[data-testid="task-counts"]').text();
    expect(counts).toContain("已完成 4 / 14");
    expect(counts).toContain("进行中 1");
    expect(counts).toContain("未开始 9");
    wrapper.unmount();
  });
});

// interrupted 是后端公开的**可恢复**状态（task_status.py 公开枚举、
// store_flow_core.py 把它归入活动态），之前既没有文案分支也没有图标分支：
// 同一张卡上轨道头部写「已中断」、卡体写「运行中」还挂着转圈动画。
describe("TaskProgress 可恢复中断态与排队态", () => {
  it("裸 interrupted 显示「已中断」且不转圈，但计时仍按未结束呈现", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "interrupted",
          progress: { overall_percent: 42, current: 3, total: 7 },
          active_elapsed_ms: 5000,
        }) as never,
        kind: "scrape",
      },
    });

    expect(wrapper.get(".task-status").text()).toContain("已中断");
    expect(wrapper.get(".task-status").text()).not.toContain("运行中");
    expect(wrapper.find(".task-status .spin").exists()).toBe(false);
    // 不得把 interrupted 当终态：用时仍是「已用」（继续后还能接着跑）
    expect(wrapper.get(".task-elapsed").text()).toContain("已用");
    wrapper.unmount();
  });

  it("queued 显示「等待开始」而不是「运行中」", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "queued",
          progress: { overall_percent: 0, current: 0, total: 0 },
        }) as never,
        kind: "scrape",
      },
    });

    expect(wrapper.get(".task-status").text()).toContain("等待开始");
    // 「等待开始」没有证据可说，不得用转圈把它演成在跑。
    expect(wrapper.find(".task-status .spin").exists()).toBe(false);
    wrapper.unmount();
  });

  // 本段跑过（有 run 身份）但这一轮读不到状态：既不能说没开始，也不能编一句
  // 「正在准备任务…」的进行中旁白，更不能转圈演成在跑。
  it("读不到状态的段只说「状态更新中」，不写「正在准备任务…」也不转圈", () => {
    const wrapper = mount(TaskProgress, {
      props: {
        snapshot: snapshot({
          status: "unknown",
          progress: {},
          total: 0,
        }) as never,
        kind: "scrape",
      },
    });

    expect(wrapper.get(".task-status").text()).toContain("状态更新中");
    expect(wrapper.get(".task-status").text()).not.toContain("运行中");
    expect(wrapper.text()).not.toContain("正在准备任务");
    expect(wrapper.find(".task-status .spin").exists()).toBe(false);
    wrapper.unmount();
  });
});

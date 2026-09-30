import { mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import path from "node:path";
import { nextTick } from "vue";
import ParallelPlatformProgress from "../ParallelPlatformProgress.vue";

describe("ParallelPlatformProgress", () => {
  const snapshots = {
    running: { status: "running", progress: { overall_percent: 42, stage: "scrape", current: 3, total: 7 }, logs: [] },
    paused: { status: "paused", progress: { overall_percent: 61, stage: "screen" }, logs: [], pause_info: { error_code: "user_paused", error_reason: "已暂停" } },
    failed: { status: "failed", progress: {}, logs: [], error: "AI unavailable" },
  };

  it("renders independent BOSS and Zhilian items and only emits the clicked action", async () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          { platform: "boss", trackId: "b", runId: "run-b", kind: "scrape", stage: "scrape", status: "running", snapshot: snapshots.running },
          { platform: "zhilian", trackId: "z", runId: "run-z", kind: "screen", stage: "screen", status: "paused", snapshot: snapshots.paused },
        ],
        busyPlatform: "boss",
      },
    });
    expect(wrapper.find('[data-testid="parallel-track-boss"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="parallel-track-zhilian"]').exists()).toBe(true);
    await wrapper.get('[data-testid="parallel-zhilian-resume"]').trigger("click");
    await nextTick();
    expect(wrapper.emitted("action")).toEqual([["zhilian", "resume"]] as never);
    expect(wrapper.find('[data-testid="parallel-zhilian-resume"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="parallel-boss-pause"]').attributes("disabled")).toBeDefined();
  });

  it("shows a failed track through TaskProgress instead of a fake completed bar", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          { platform: "zhilian", trackId: "z", runId: "run-z", kind: "scrape", stage: "scrape", status: "failed", snapshot: snapshots.failed },
        ],
      },
    });
    expect(wrapper.text()).toContain("智联");
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false);
  });

  // 终态文案以后端 flow_tracks 白名单为唯一口径：stopped / cancelled 都是真实
  // 终态（停止与取消轨道由 store_flow_claims / store_flow_state 写入），
  // 之前落到兜底分支显示「状态更新中」，用户看不出这一线已经停了。
  it.each([
    ["stopped", "run-stopped"],
    ["cancelled", "run-cancelled"],
  ])("shows a %s Track as a terminal stopped state without a spinner", async (status, runId) => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          {
            platform: "boss",
            trackId: `${status}-track`,
            runId,
            kind: "scrape",
            stage: "scrape",
            status,
            snapshot: { ...snapshots.running, status: "running" },
          },
        ],
      },
    });

    expect(wrapper.get('[data-testid="parallel-track-status"]').text()).toBe("已停止");
    expect(wrapper.find('[data-testid="parallel-track-boss"] .task-status .spin').exists()).toBe(false);
    expect(wrapper.find('[data-testid="parallel-boss-pause"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="parallel-boss-stop"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("renders each item with the original TaskProgress and real snapshot data", async () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          { platform: "boss", trackId: "track-b", runId: "run-b", kind: "scrape", stage: "scrape", status: "running", snapshot: snapshots.running },
          { platform: "zhilian", trackId: "track-z", runId: "run-z", kind: "screen", stage: "screen", status: "paused", snapshot: snapshots.paused },
        ],
        busyPlatform: "boss",
      },
    });
    const tasks = wrapper.findAllComponents({ name: "TaskProgress" });
    expect(tasks).toHaveLength(2);
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false);
    expect(wrapper.find(".parallel-platform-bar").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("scrape");
    expect(wrapper.text()).not.toContain("screen");
    await wrapper.get('[data-testid="parallel-zhilian-resume"]').trigger("click");
    expect(wrapper.emitted("action")).toEqual([["zhilian", "resume"]] as never);
    expect(wrapper.find('[data-testid="parallel-boss-pause"]').attributes("disabled")).toBeDefined();
  });

  it("keeps one vertical single-column shell without fake percentages", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: { items: [{ platform: "boss", trackId: "b", runId: "r", kind: "scrape", stage: "scrape", status: "running", snapshot: snapshots.running }] },
    });
    const source = (wrapper.element as HTMLElement).outerHTML;
    expect(source).not.toContain("repeat(2");
    expect(source).not.toContain("35%");
    expect(source).not.toContain("65%");
  });

  it("uses the explicit Flow kind instead of guessing from an opaque run id", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          platform: "boss", trackId: "b", runId: "8c0f0c9e-opaque", kind: "screen", stage: "screen",
          status: "running", snapshot: { ...snapshots.running, status: "running" },
        }],
      },
    });
    const task = wrapper.findComponent({ name: "TaskProgress" });
    expect(task.props("kind")).toBe("screen");
    expect(wrapper.text()).not.toContain("running");
  });

  it("disables every Track action while Flow state is stale", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        stale: true,
        items: [{
          platform: "boss", trackId: "stale-track", runId: "run-stale", kind: "scrape", stage: "scrape",
          status: "paused", snapshot: snapshots.paused,
        }],
      },
    });

    expect(wrapper.get('[data-testid="parallel-boss-resume"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-testid="parallel-boss-stop"]').attributes("disabled")).toBeDefined();
  });

  // 轨道头部用 Track 状态覆盖快照状态，卡体走 TaskProgress 自己的口径：
  // interrupted 之前只在头部有「已中断」、卡体落到「运行中」还转圈，
  // 同一张卡自相矛盾。两处必须说同一句话，且都不得转圈。
  it("shows one 已中断 wording in both the track header and the TaskProgress body", async () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          platform: "boss", trackId: "interrupted-track", runId: "run-interrupted", kind: "scrape",
          stage: "scrape", status: "interrupted",
          snapshot: { ...snapshots.running, status: "interrupted" },
        }],
      },
    });

    expect(wrapper.get('[data-testid="parallel-track-status"]').text()).toBe("已中断");
    expect(wrapper.get('[data-testid="parallel-track-boss"] .task-status').text()).toContain("已中断");
    expect(wrapper.get('[data-testid="parallel-track-boss"] .task-status').text()).not.toContain("运行中");
    expect(wrapper.find('[data-testid="parallel-track-boss"] .task-status .spin').exists()).toBe(false);
    // interrupted 是可恢复态：继续按钮必须在，不得被当终态收掉。
    expect(wrapper.find('[data-testid="parallel-boss-resume"]').exists()).toBe(true);
    wrapper.unmount();
  });

  // 阶段卡说的是这一段的事：抓取段自己已经跑完，整条线往下走到 AI 段还在跑，
  // 这张卡的头部与卡体都必须说「这一段完成了」——轨道状态不再只归头部，
  // 它归「线当前所在的那一段」那张卡（carriesLineState 由 Flow 呈现层判定并下发）。
  it("freezes a finished scrape stage while its Track is still running", () => {
    const now = Date.now();
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          platform: "zhilian", trackId: "live-track", runId: "run-scrape-done", kind: "scrape",
          stage: "scrape", status: "running", carriesLineState: false,
          snapshot: {
            ...snapshots.running,
            status: "succeeded",
            progress: { overall_percent: 100, stage: "done", current: 7, total: 7 },
            started_at: now - 60_000,
            finished_at: now - 1_000,
          },
        }],
      },
    });

    expect(wrapper.get('[data-testid="parallel-track-status"]').text()).toBe("已完成");
    const body = wrapper.get('[data-testid="parallel-track-zhilian"] .task-status').text();
    expect(body).not.toContain("抓取中");
    expect(body).not.toContain("运行中");
    expect(body).not.toContain("进行中");
    expect(wrapper.find('[data-testid="parallel-track-zhilian"] .task-status .spin').exists()).toBe(false);
    expect(wrapper.get('[data-testid="parallel-track-zhilian"]').text()).not.toContain("已用");
    wrapper.unmount();
  });

  // 真实缺陷：智联轨道 status=interrupted、阶段在 AI（服务重启打断筛选），
  // 抓取 run 早在重启前就 100% 成功。02 页「智联 抓取」卡头部写「已中断」、
  // 卡体写「完整成功」，无障碍朗读连成「智联 抓取 已中断 完整成功，列表抓取，
  // 已抓取 377 个岗位」。轨道级状态只归中断发生的那一段，别的段不背这个锅。
  it("routes the Track-level 已中断 badge to the stage the interruption actually hit", () => {
    const scrape = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          platform: "zhilian", trackId: "interrupted-line", runId: "run-scrape-377", kind: "scrape",
          stage: "scrape", status: "interrupted", carriesLineState: false,
          snapshot: {
            status: "succeeded",
            progress: { overall_percent: 100, stage: "done", current: 377, total: 377 },
            logs: [],
            scraped_count: 377,
            source_total: 377,
            integrity: { conclusion: "succeeded", label: "完整成功" },
          },
        }],
      },
    });

    const scrapeCard = scrape.get('[data-testid="parallel-track-zhilian"]');
    // 头部徽章与卡体出自同一份状态词计算：这一段带着白箱「完整成功」结论，
    // 头部就只能说这一句，不再另表一句状态别名（本行原写「已完成」，是两条口径
    // 各说各话的产物；本段不背轨道中断的语义不变，见下面的 not.toContain）。
    expect(scrapeCard.get('[data-testid="parallel-track-status"]').text()).toBe("完整成功");
    expect(scrapeCard.text()).toContain("完整成功");
    // 头部与卡体不得连读成矛盾文案：这一段没有任何中断可说。
    expect(scrapeCard.text()).not.toContain("已中断");
    // 动作按钮仍按轨道状态驱动：整条线可恢复，这张卡照旧只给「继续」。
    expect(scrape.find('[data-testid="parallel-zhilian-pause"]').exists()).toBe(false);
    expect(scrape.find('[data-testid="parallel-zhilian-stop"]').exists()).toBe(false);
    expect(scrape.find('[data-testid="parallel-zhilian-resume"]').exists()).toBe(true);
    scrape.unmount();

    const screen = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          platform: "zhilian", trackId: "interrupted-line", runId: "run-ai-interrupted", kind: "screen",
          stage: "screen", status: "interrupted", carriesLineState: true,
          snapshot: { ...snapshots.running, status: "interrupted" },
        }],
      },
    });
    const screenCard = screen.get('[data-testid="parallel-track-zhilian"]');
    expect(screenCard.get('[data-testid="parallel-track-status"]').text()).toBe("已中断");
    expect(screen.find('[data-testid="parallel-zhilian-pause"]').exists()).toBe(false);
    expect(screen.find('[data-testid="parallel-zhilian-stop"]').exists()).toBe(false);
    expect(screen.find('[data-testid="parallel-zhilian-resume"]').exists()).toBe(true);
    screen.unmount();
  });

  // 中断的轨道没有活着的工人：只给恢复动作，不再给注定失败的暂停/停止。
  it("offers only the recovery action for an interrupted Track", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          platform: "zhilian", trackId: "interrupted-live-track", runId: "run-screen-interrupted",
          kind: "screen", stage: "screen", status: "interrupted",
          snapshot: { ...snapshots.running, status: "interrupted" },
        }],
      },
    });

    expect(wrapper.get('[data-testid="parallel-track-status"]').text()).toBe("已中断");
    expect(wrapper.find('[data-testid="parallel-zhilian-pause"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="parallel-zhilian-stop"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="parallel-zhilian-resume"]').exists()).toBe(true);
    wrapper.unmount();
  });

  // 同一张卡的头部与卡体必须同一套词：卡体（TaskProgress）的中文说法是唯一口径，
  // 头部状态词表逐一对齐（queued 等待开始、running 运行中、failed 执行失败、
  // partial/completed_with_pending 完成，但有待确认、pausing 正在暂停）。
  it.each([
    ["queued", "等待开始"],
    ["running", "运行中"],
    ["paused", "已暂停"],
    ["pausing", "正在暂停"],
    ["interrupted", "已中断"],
    ["failed", "执行失败"],
    ["cancelled", "已停止"],
    ["stopped", "已停止"],
    ["partial", "完成，但有待确认"],
    ["completed_with_pending", "完成，但有待确认"],
    ["succeeded", "已完成"],
    ["done", "已完成"],
    ["unavailable", "暂不可用"],
  ])("shares one %s wording between the Track header and the card body", async (status, label) => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          platform: "boss", trackId: `${status}-track`, runId: `run-${status}`, kind: "scrape",
          stage: "scrape", status,
          snapshot: { status, progress: {}, logs: [], pause_info: { error_code: "user_paused", error_reason: "已暂停" } },
        }],
      },
    });

    expect(wrapper.get('[data-testid="parallel-track-status"]').text()).toBe(label);
    expect(wrapper.get('[data-testid="parallel-track-boss"] .task-status').text()).toContain(label);
    wrapper.unmount();
  });

  // 真实缺陷：轨道已经停止，本段快照还停在「正在暂停」——头部说「已停止」、
  // 卡体说「正在暂停」，朗读连成两句互不相容的话。轨道状态往下压清单漏了
  // pausing（呈现层把它算在飞），补齐后两处同说「已停止」且不转圈。
  it("pushes a stopped Track down onto a stage still pausing in both header and body", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          platform: "boss", trackId: "pausing-track", runId: "run-pausing", kind: "screen",
          stage: "screen", status: "stopped", carriesLineState: true,
          snapshot: { ...snapshots.running, status: "pausing", progress: {} },
        }],
      },
    });

    expect(wrapper.get('[data-testid="parallel-track-status"]').text()).toBe("已停止");
    expect(wrapper.get('[data-testid="parallel-track-boss"] .task-status').text()).toContain("已停止");
    expect(wrapper.get('[data-testid="parallel-track-boss"] .task-status').text()).not.toContain("正在暂停");
    expect(wrapper.find('[data-testid="parallel-track-boss"] .task-status .spin').exists()).toBe(false);
    wrapper.unmount();
  });

  // 交接已发生、这张卡既不是当前段又拿不到本段证据，但它带着 runId——本段真的跑过，
  // 只是这一轮读不到状态。说「等待开始」等于把跑过的段说成还没开始，progress 被清空
  // 后卡体还会补一句「正在准备任务…」，同一张卡于是又是一套自相矛盾的话。
  // 呈现层对这种卡下发读不到状态的兜底口径，头部与卡体同说「状态更新中」，都不转圈。
  it("keeps a handed-off stage card that has a run but no readable state on the 状态更新中 fallback", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          platform: "zhilian", trackId: "handed-off-track", runId: "run-scrape-blind", kind: "scrape",
          stage: "scrape", status: "interrupted", carriesLineState: false,
          snapshot: { status: "unknown", progress: {}, logs: [] },
        }],
      },
    });

    const card = wrapper.get('[data-testid="parallel-track-zhilian"]');
    expect(card.get('[data-testid="parallel-track-status"]').text()).toBe("状态更新中");
    expect(card.get(".task-status").text()).toContain("状态更新中");
    expect(card.text()).not.toContain("等待开始");
    expect(card.text()).not.toContain("正在准备任务");
    expect(card.text()).not.toContain("已中断");
    expect(card.text()).not.toContain("已停止");
    expect(card.text()).not.toContain("执行失败");
    expect(card.find(".task-status .spin").exists()).toBe(false);
    // 动作仍按轨道状态驱动：整条线可恢复。
    expect(wrapper.find('[data-testid="parallel-zhilian-resume"]').exists()).toBe(true);
    wrapper.unmount();
  });

  // 另一种无证据：这一段连 run 身份都还没有（呈现层此时才给中性 queued），
  // 「等待开始」是事实，但也不许演成在跑。
  it("keeps a handed-off stage card that never got a run on the neutral wording", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          platform: "zhilian", trackId: "never-started-track", runId: "", kind: "scrape",
          stage: "scrape", status: "interrupted", carriesLineState: false,
          snapshot: { status: "queued", progress: {}, logs: [] },
        }],
      },
    });

    const card = wrapper.get('[data-testid="parallel-track-zhilian"]');
    expect(card.get('[data-testid="parallel-track-status"]').text()).toBe("等待开始");
    expect(card.get(".task-status").text()).toContain("等待开始");
    expect(card.text()).not.toContain("已中断");
    expect(card.text()).not.toContain("已停止");
    expect(card.text()).not.toContain("执行失败");
    expect(card.find(".task-status .spin").exists()).toBe(false);
    expect(wrapper.find('[data-testid="parallel-zhilian-resume"]').exists()).toBe(true);
    wrapper.unmount();
  });

  // webui/task_status.py 把白箱 unverifiable 公开成 completed_with_pending，于是头部
  // 按状态别名说「完成，但有待确认」、卡体按白箱结论说「无法确认是否完成」并走红叉——
  // 同一张卡两句不同结论，头部把不确定报成了完成。两处状态词必须出自同一份计算，
  // 且不得为了让字面相同把卡体的白箱口径改写成「完成，但有待确认」（丢白箱信号）。
  it("says one single conclusion on a card whose whitebox verdict is unverifiable", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          platform: "boss", trackId: "unverifiable-track", runId: "run-unverifiable", kind: "screen",
          stage: "screen", status: "completed_with_pending",
          snapshot: {
            status: "completed_with_pending", progress: { overall_percent: 100 }, logs: [],
            integrity: { conclusion: "unverifiable", label: "无法确认", primary_reason: "证据不足" },
          },
        }],
      },
    });

    const card = wrapper.get('[data-testid="parallel-track-boss"]');
    expect(card.get('[data-testid="parallel-track-status"]').text()).toBe("无法确认是否完成");
    expect(card.get(".task-status").text()).toContain("无法确认是否完成");
    expect(card.text()).not.toContain("完成，但有待确认");
    wrapper.unmount();
  });

  // 并行时两张卡同时可见，每两秒轮询一次计数一变就各播一句；播报没有主语时
  // 听者分不清是哪条线。播报必须带上本平台显示名，且不把百分比/用时卷进去。
  it("names the platform in each card's accessibility announcement", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          {
            platform: "boss", trackId: "announce-b", runId: "run-announce-b", kind: "scrape",
            stage: "scrape", status: "running",
            snapshot: { status: "running", progress: { overall_percent: 40, current: 4, total: 10, stage: "scrape" }, logs: [], scraped_count: 40 },
          },
          {
            platform: "zhilian", trackId: "announce-z", runId: "run-announce-z", kind: "scrape",
            stage: "scrape", status: "running",
            snapshot: { status: "running", progress: { overall_percent: 20, current: 2, total: 10, stage: "scrape" }, logs: [], scraped_count: 20 },
          },
        ],
      },
    });

    const announcements = wrapper.findAll('[data-testid="task-progress-announcement"]').map((node) => node.text());
    expect(announcements).toHaveLength(2);
    expect(announcements[0]).toContain("BOSS");
    expect(announcements[1]).toContain("智联");
    expect(announcements[0]).not.toContain("%");
    expect(announcements[1]).not.toContain("秒");
    wrapper.unmount();
  });

  // 只允许一份口径：在飞状态清单此前在呈现层（含空串）与本组件（不含空串）各写一份，
  // 活动态清单也各写一份，两份会各自漂移（历史上就漏过 pausing）。收敛进树干
  // discovery.ts 后，消费方只许导入不许再自己声明；当前段判定（ownsTrackState /
  // AI 阶段族集合）也必须只有呈现层一处定义。
  it("shares one status vocabulary with the Flow presentation layer instead of declaring its own", () => {
    const component = readFileSync(path.join(__dirname, "../ParallelPlatformProgress.vue"), "utf8");
    const body = readFileSync(path.join(__dirname, "../TaskProgress.vue"), "utf8");
    const presentation = readFileSync(path.join(__dirname, "../../composables/useDiscoveryFlowPresentation.ts"), "utf8");
    const trunk = readFileSync(path.join(__dirname, "../../discovery.ts"), "utf8");

    expect(trunk).toMatch(/export const STAGE_IN_FLIGHT_STATUSES/);
    expect(trunk).toMatch(/export const ACTIVE_TRACK_STATUSES/);
    for (const consumer of [component, presentation, body]) {
      expect(consumer).not.toMatch(/const STAGE_IN_FLIGHT_STATUSES\s*=/);
      expect(consumer).not.toMatch(/const ACTIVE_TRACK_STATUSES\s*=/);
      expect(consumer).toMatch(/from "\.\.\/discovery"/);
    }
    // 当前段判定只有一处：组件与卡体都不得再自带一套阶段清单或第二个 ownsTrackState。
    expect(component).not.toMatch(/AI_FAILURE_STAGES|ownsTrackState|isAiCurrentStage/);
    expect(body).not.toMatch(/AI_FAILURE_STAGES|ownsTrackState|isAiCurrentStage/);
    expect((presentation.match(/const AI_FAILURE_STAGES\s*=/g) ?? []).length).toBe(1);
  });
});

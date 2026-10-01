import { mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import path from "node:path";
import ParallelPlatformProgress from "../ParallelPlatformProgress.vue";
import ScreenRoundActions from "../ScreenRoundActions.vue";
import { deriveTrackActionBar } from "../../screenFlow";
import type { Platform } from "../../types";

// 轨道行是「一平台一行」的复用现场：进度卡与动作条都是既有组件，
// 这一层只把呈现层算好的事实摆出来、把点击原样往上传。
// 动作事实由呈现层按轨道各算一份（见 useDiscoveryFlowPresentation.spec），
// 本文件因此直接喂派生结果，不再自带任何状态判定。
function snapshotFor(status: string, progress: Record<string, unknown> = {}) {
  return { status, progress, logs: [] };
}

function item(overrides: Record<string, unknown> = {}) {
  const platform = (overrides.platform as Platform) || "boss";
  const kind = (overrides.kind as "scrape" | "screen") || "scrape";
  const status = String(overrides.status ?? "running");
  const runId = String(overrides.runId ?? `run-${platform}`);
  const bar = deriveTrackActionBar({ stage: kind, status, runId });
  return {
    platform,
    trackId: `track-${platform}`,
    runId,
    kind,
    stage: kind,
    status,
    snapshot: snapshotFor(status, { overall_percent: 42, current: 3, total: 7 }),
    enteredAt: 0,
    ...bar,
    finishRunId: runId,
    finishTestId: `parallel-track-${platform}-finish-save`,
    cancelTestId: `parallel-track-${platform}-cancel`,
    ...overrides,
  };
}

function row(wrapper: ReturnType<typeof mount>, platform: Platform) {
  return wrapper.get(`[data-testid="parallel-track-${platform}"]`);
}

describe("ParallelPlatformProgress", () => {
  it("renders one shared action bar per Track row, each with that Track's own action", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          item({ platform: "boss", status: "running" }),
          item({
            platform: "zhilian",
            kind: "screen",
            stage: "screen",
            status: "paused",
            snapshot: snapshotFor("paused"),
          }),
        ],
      },
    });

    expect(wrapper.findAllComponents({ name: "TaskProgress" })).toHaveLength(2);
    const bars = wrapper.findAllComponents(ScreenRoundActions);
    expect(bars).toHaveLength(2);
    expect(bars[0]?.props("action")).toEqual({ kind: "pause-scrape", label: "暂停" });
    expect(bars[1]?.props("action")).toEqual({ kind: "continue", label: "继续 AI 筛选" });
    expect(row(wrapper, "boss").text()).toContain("暂停");
    expect(row(wrapper, "zhilian").text()).toContain("继续 AI 筛选");
    wrapper.unmount();
  });

  // D-04：动作条是这张卡的一部分——落进卡边框内部，左缘随卡内 padding 与卡内内容对齐。
  it("renders each Track's action bar inside that Track's own progress card", () => {
    const component = readFileSync(path.join(__dirname, "../ParallelPlatformProgress.vue"), "utf8");
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          item({ platform: "boss" }),
          item({ platform: "zhilian", kind: "screen", stage: "screen" }),
        ],
      },
    });

    expect(wrapper.findAll("section.task-progress")).toHaveLength(2);
    for (const platform of ["boss", "zhilian"] as Platform[]) {
      const trackRow = row(wrapper, platform);
      const card = trackRow.get("section.task-progress");
      const bars = card.findAllComponents(ScreenRoundActions);
      expect(bars).toHaveLength(1);
      // DOM 包含关系：动作行在卡边框之内，不是掉在卡外的兄弟节点。
      expect(card.element.contains(bars[0]!.element)).toBe(true);
      // 平台标识仍然只有卡自己那一份（表头删掉后徽章没有跟着消失）。
      expect(trackRow.get('[data-testid="task-platform-badge"]').text()).toContain(
        platform === "boss" ? "BOSS" : "智联",
      );
    }

    // 结构：动作条写在 <TaskProgress> 的开合标签之间（作为卡自己的落点内容）。
    const cardOpen = component.indexOf("<TaskProgress");
    const cardClose = component.indexOf("</TaskProgress>");
    expect(cardOpen).toBeGreaterThanOrEqual(0);
    expect(cardClose).toBeGreaterThan(cardOpen);
    expect(component.indexOf("<ScreenRoundActions")).toBeGreaterThan(cardOpen);
    expect(component.indexOf("<ScreenRoundActions")).toBeLessThan(cardClose);
    wrapper.unmount();
  });

  // D-04：自写表头删除后，一行里表示平台/阶段/状态的元素只允许来自 TaskProgress。
  it("leaves the progress card as the only voice of a Track's platform, stage and status", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          item({ platform: "boss", status: "paused", snapshot: snapshotFor("paused") }),
          item({ platform: "zhilian", status: "running", snapshot: snapshotFor("running") }),
        ],
      },
    });

    expect(wrapper.find('[data-testid="parallel-track-status"]').exists()).toBe(false);
    expect(wrapper.find(".parallel-item-header").exists()).toBe(false);
    for (const platform of ["boss", "zhilian"] as Platform[]) {
      const trackRow = row(wrapper, platform);
      // 同一行内表示状态的元素只有一个，且它就是卡体的那一个。
      expect(trackRow.findAll(".task-status")).toHaveLength(1);
      // 行内不再有第二层表头（卡自己的那一个 header 保留）。
      expect(trackRow.findAll("header")).toHaveLength(1);
    }
    expect(row(wrapper, "boss").get(".task-status").text()).toBe("已暂停");
    wrapper.unmount();
  });

  it("emits only the clicked Track's platform and action kind", async () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          item({ platform: "boss", status: "running" }),
          item({ platform: "zhilian", status: "paused", snapshot: snapshotFor("paused") }),
        ],
      },
    });

    await row(wrapper, "boss").get('[data-testid="pause-scrape"]').trigger("click");
    expect(wrapper.emitted("action")).toEqual([["boss", "pause-scrape"]] as never);

    await row(wrapper, "zhilian").get('[data-testid="continue-scrape"]').trigger("click");
    expect(wrapper.emitted("action")).toEqual([
      ["boss", "pause-scrape"],
      ["zhilian", "continue-scrape"],
    ] as never);
    wrapper.unmount();
  });

  // 轨道级「结束并保存」走单平台同一条 run 级收尾：把这条线自己的 run id 交给页面，
  // 由页面调用既有的 finishPausedTask，不新增端点、不新增一套动作。
  it("hands the Track's own run id to the shared finish path", async () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: { items: [item({ platform: "zhilian", runId: "scrape-zhilian" })] },
    });

    await row(wrapper, "zhilian").get('[data-testid="parallel-track-zhilian-finish-save"]').trigger("click");
    expect(wrapper.emitted("finish")).toEqual([["scrape-zhilian"]] as never);
    expect(wrapper.emitted("action")).toBeUndefined();
    wrapper.unmount();
  });

  it("labels the Track cancel action 终止本轨 and emits cancel for that platform only", async () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          item({ platform: "boss", status: "paused", snapshot: snapshotFor("paused") }),
          item({ platform: "zhilian", status: "paused", snapshot: snapshotFor("paused") }),
        ],
      },
    });

    expect(row(wrapper, "boss").get('[data-testid="parallel-track-boss-cancel"]').text()).toContain("终止本轨");
    await row(wrapper, "boss").get('[data-testid="parallel-track-boss-cancel"]').trigger("click");
    expect(wrapper.emitted("action")).toEqual([["boss", "cancel"]] as never);
    wrapper.unmount();
  });

  // 状态词表：中断＝无活体 worker，只能开新一轮（页面级「开始新一轮」出口），
  // 轨道行上不得再出现「继续」。这里喂呈现层给的中断事实，断言本行只剩收尾出口。
  it("gives an interrupted Track the close-out exit and no continuation", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [item({
          platform: "boss",
          status: "interrupted",
          snapshot: snapshotFor("interrupted"),
        })],
      },
    });

    const actions = row(wrapper, "boss").get(".screen-round-actions");
    expect(actions.find('[data-testid="continue-scrape"]').exists()).toBe(false);
    expect(actions.find('[data-testid="pause-scrape"]').exists()).toBe(false);
    expect(actions.text()).not.toContain("继续");
    expect(actions.get('[data-testid="parallel-track-boss-finish-save"]').text()).toContain("结束并保存");
    wrapper.unmount();
  });

  it("renders a terminal Track row without any action button", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [item({
          platform: "boss", status: "stopped", snapshot: snapshotFor("stopped"),
        })],
      },
    });

    expect(wrapper.findAllComponents(ScreenRoundActions)).toHaveLength(1);
    expect(row(wrapper, "boss").findAll("button")).toHaveLength(0);
    wrapper.unmount();
  });

  it("keeps the shared busy and read-only facts working for every button on that row", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          item({ platform: "boss", status: "paused", snapshot: snapshotFor("paused") }),
          item({ platform: "zhilian", status: "paused", snapshot: snapshotFor("paused") }),
        ],
        busyPlatform: "boss",
      },
    });

    expect(row(wrapper, "boss").findAll("button").every((button) => button.attributes("disabled") !== undefined)).toBe(true);
    expect(row(wrapper, "zhilian").findAll("button").every((button) => button.attributes("disabled") === undefined)).toBe(true);
    wrapper.unmount();
  });

  // 046 D-03 / FR-015：现场只读（流程状态读不到、本轮已锁定）锁的是这条线的主动作，
  // 不是用户唯一的收口出路。「结束并保存结果」与「终止本轨」在没有请求在飞时必须仍可点，
  // 否则这一轮就只能靠刷新页面离开这里。按钮位置不变，仍在这条线的卡里。
  it("keeps the two close-out exits clickable while only the primary action is read-only", async () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        stale: true,
        items: [item({ platform: "boss", status: "paused", snapshot: snapshotFor("paused") })],
      },
    });

    const buttons = row(wrapper, "boss").findAll("button");
    expect(buttons.length).toBeGreaterThan(0);
    expect(row(wrapper, "boss").get('[data-testid="continue-scrape"]').attributes("disabled")).toBeDefined();

    const finish = row(wrapper, "boss").get('[data-testid="parallel-track-boss-finish-save"]');
    const cancel = row(wrapper, "boss").get('[data-testid="parallel-track-boss-cancel"]');
    expect(finish.attributes("disabled")).toBeUndefined();
    expect(cancel.attributes("disabled")).toBeUndefined();

    await finish.trigger("click");
    await cancel.trigger("click");
    expect(wrapper.emitted("finish")).toEqual([["run-boss"]] as never);
    expect(wrapper.emitted("action")).toEqual([["boss", "cancel"]] as never);
    wrapper.unmount();
  });

  // 反向配对：真的有请求在飞时，收尾两条按忙态锁住（不许把这条当成只读锁来删）。
  it("still locks both close-out exits while one of them is in flight", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [item({ platform: "boss", status: "paused", snapshot: snapshotFor("paused") })],
        finishBusy: true,
      },
    });

    expect(row(wrapper, "boss").get('[data-testid="parallel-track-boss-finish-save"]').attributes("disabled")).toBeDefined();
    expect(row(wrapper, "boss").get('[data-testid="parallel-track-boss-cancel"]').attributes("disabled")).toBeDefined();
    wrapper.unmount();
  });

  it("says the interrupted Track's wording once, through the card's own status label", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [item({
          platform: "boss", status: "interrupted", snapshot: snapshotFor("interrupted"),
        })],
      },
    });

    // 表头已删：这一行表示状态的元素只允许来自 TaskProgress，且只有一处。
    const statuses = row(wrapper, "boss").findAll(".task-status");
    expect(statuses).toHaveLength(1);
    expect(statuses[0]?.text()).toBe("已中断");
    expect(row(wrapper, "boss").find(".task-status .spin").exists()).toBe(false);
    wrapper.unmount();
  });

  it("renders the stage card exactly as the presentation layer resolved it", () => {
    // 抓取段自己已完成、整条线在 AI 段暂停：头部徽章与卡体都只说这一段（呈现层已定稿），
    // 动作仍按轨道状态给暂停 AI（轨道级事实）。组件不得再自己压一遍状态。
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          ...item({ platform: "zhilian", status: "paused", runId: "run-scrape-done" }),
          snapshot: { ...snapshotFor("succeeded"), progress: { overall_percent: 100, current: 7, total: 7 } },
        }],
      },
    });

    expect(row(wrapper, "zhilian").get(".task-status").text()).toBe("已完成");
    expect(row(wrapper, "zhilian").text()).not.toContain("已暂停");
    expect(row(wrapper, "zhilian").text()).not.toContain("已中断");
    expect(row(wrapper, "zhilian").find(".task-status .spin").exists()).toBe(false);
    wrapper.unmount();
  });

  it("shows a failed track through TaskProgress instead of a fake completed bar", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          ...item({ platform: "zhilian", status: "failed" }),
          snapshot: { status: "failed", progress: {}, logs: [], error: "AI unavailable" },
        }],
      },
    });
    expect(wrapper.text()).toContain("智联");
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("renders each item with the original TaskProgress and real snapshot data", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: { items: [item({ platform: "boss" }), item({ platform: "zhilian", kind: "screen", stage: "screen" })] },
    });
    expect(wrapper.findAllComponents({ name: "TaskProgress" })).toHaveLength(2);
    expect(wrapper.find(".parallel-platform-bar").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("scrape");
    expect(wrapper.text()).not.toContain("screen");
    wrapper.unmount();
  });

  it("keeps one vertical single-column shell without fake percentages", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: { items: [item({ platform: "boss" })] },
    });
    const source = (wrapper.element as HTMLElement).outerHTML;
    expect(source).not.toContain("repeat(2");
    expect(source).not.toContain("35%");
    expect(source).not.toContain("65%");
    wrapper.unmount();
  });

  it("uses the explicit Flow kind instead of guessing from an opaque run id", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: { items: [item({ platform: "boss", runId: "8c0f0c9e-opaque", kind: "screen", stage: "screen" })] },
    });
    const task = wrapper.findComponent({ name: "TaskProgress" });
    expect(task.props("kind")).toBe("screen");
    expect(wrapper.text()).not.toContain("running");
    wrapper.unmount();
  });

  // 白箱结论与状态别名同出一处：口径由卡自己按唯一词表算，头部与卡体不许两句结论。
  it("says one single conclusion on a card whose whitebox verdict is unverifiable", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [{
          ...item({ platform: "boss", kind: "screen", stage: "screen", status: "completed_with_pending" }),
          snapshot: {
            status: "completed_with_pending", progress: { overall_percent: 100 }, logs: [],
            integrity: { conclusion: "unverifiable", label: "无法确认", primary_reason: "证据不足" },
          },
        }],
      },
    });

    const card = row(wrapper, "boss");
    // 一句结论：行内表示状态的元素只剩卡体那一个，头部不再另说一遍。
    expect(card.findAll(".task-status")).toHaveLength(1);
    expect(card.get(".task-status").text()).toContain("无法确认是否完成");
    expect(card.text()).not.toContain("完成，但有待确认");
    wrapper.unmount();
  });

  // 并行时两张卡同时可见，播报必须带上本平台显示名，且不把百分比/用时卷进去。
  it("names the platform in each card's accessibility announcement", () => {
    const wrapper = mount(ParallelPlatformProgress, {
      props: {
        items: [
          item({ platform: "boss", snapshot: { status: "running", progress: { overall_percent: 40, current: 4, total: 10, stage: "scrape" }, logs: [], scraped_count: 40 } }),
          item({ platform: "zhilian", snapshot: { status: "running", progress: { overall_percent: 20, current: 2, total: 10, stage: "scrape" }, logs: [], scraped_count: 20 } }),
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

  // D-03 结构收敛：并行轨道不许再自带一套动作条与状态判定。
  // 负向断言的每一条都配一条「复用还在」的正向断言，防止删功能换绿灯。
  it("reuses the shared progress card and action bar instead of a parallel-only control set", () => {
    const component = readFileSync(path.join(__dirname, "../ParallelPlatformProgress.vue"), "utf8");
    const wrapper = mount(ParallelPlatformProgress, { props: { items: [item({ platform: "boss" })] } });

    // 正向：每行都是既有 TaskProgress + 既有 ScreenRoundActions。
    expect(component).toMatch(/import TaskProgress from "\.\/TaskProgress\.vue"/);
    expect(component).toMatch(/import ScreenRoundActions from "\.\/ScreenRoundActions\.vue"/);
    expect(wrapper.findAllComponents({ name: "TaskProgress" })).toHaveLength(1);
    expect(wrapper.findAllComponents(ScreenRoundActions)).toHaveLength(1);
    // 正向：动作仍有出口，页面据此发到后端。
    expect(component).toMatch(/defineEmits/);
    expect(component).toMatch(/emit\("action"/);
    expect(component).toMatch(/emit\(['"]finish['"]/);

    // 负向：没有裸按钮、没有自写显隐、没有第二套状态判定。
    expect((component.match(/<button/g) ?? []).length).toBe(0);
    expect(component).not.toMatch(/visibleButtons/);
    for (const status of ["queued", "running", "pausing", "paused", "interrupted", "failed", "stopped", "cancelled", "succeeded", "done", "partial", "completed_with_pending", "unavailable"]) {
      expect(component.includes(`"${status}"`)).toBe(false);
    }
    wrapper.unmount();
  });

  // 只允许一份口径：在飞/活动态清单与当前段判定都不许在这一层重复。
  it("shares one status vocabulary with the Flow presentation layer instead of declaring its own", () => {
    const component = readFileSync(path.join(__dirname, "../ParallelPlatformProgress.vue"), "utf8");
    const body = readFileSync(path.join(__dirname, "../TaskProgress.vue"), "utf8");
    const presentation = readFileSync(path.join(__dirname, "../../composables/useDiscoveryFlowPresentation.ts"), "utf8");
    const trunk = readFileSync(path.join(__dirname, "../../discovery.ts"), "utf8");

    expect(trunk).toMatch(/export const STAGE_IN_FLIGHT_STATUSES/);
    expect(trunk).toMatch(/export const ACTIVE_TRACK_STATUSES/);
    for (const consumer of [body, presentation]) {
      expect(consumer).not.toMatch(/const STAGE_IN_FLIGHT_STATUSES\s*=/);
      expect(consumer).not.toMatch(/const ACTIVE_TRACK_STATUSES\s*=/);
      expect(consumer).toMatch(/from "\.\.\/discovery"/);
    }
    // 轨道行不解释状态：清单、当前段判定与状态词都不许出现在这一层。
    expect(component).not.toMatch(/AI_FAILURE_STAGES|ownsTrackState|isAiCurrentStage/);
    expect(component).not.toMatch(/STAGE_IN_FLIGHT_STATUSES|ACTIVE_TRACK_STATUSES/);
    expect(body).not.toMatch(/AI_FAILURE_STAGES|ownsTrackState|isAiCurrentStage/);
    expect((presentation.match(/const AI_FAILURE_STAGES\s*=/g) ?? []).length).toBe(1);
    // 正向：状态词仍然只有 discovery.ts 一份，由卡自己算（头部与卡体同出一处）。
    expect(trunk).toMatch(/export function stageStatusLabel/);
    expect(body).toMatch(/stageStatusLabel/);
    // 呈现层只定稿「这一段说什么状态」，那句话的措辞不再在中间层抄第二份。
    expect(presentation).not.toMatch(/stageStatusLabel\(/);
    expect(presentation).not.toMatch(/const STAGE_STATUS_LABELS|const STATUS_LABELS/);
  });
});

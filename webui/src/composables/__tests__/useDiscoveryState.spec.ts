// 035 T002：liveTaskStep 只读派生——未结束任务的「真实进度页」统一判定面。
// 抓取活 → "search"（02）；筛选/重抓活 → "screen"（03）；无活任务 → ""。
// US2（入口守卫跳回落点）与 US3（回最新落点/按钮一致性）共用。
import { ref } from "vue";
import {
  deriveLiveTaskStep,
  liveTaskStep,
  useDiscoveryState,
} from "../useDiscoveryState";
import type { DiscoveryState } from "../useDiscoveryState";

function makeState(overrides: Partial<DiscoveryState> = {}): DiscoveryState {
  const state = useDiscoveryState({ profileId: "test" }, () => {});
  return Object.assign(state, overrides);
}

describe("useDiscoveryState.liveTaskStep（035 真实进度页派生）", () => {
  it("① 仅抓取活（scrapeBusy）→ search（02）", () => {
    const state = makeState({ scrapeBusy: ref(true) });
    expect(liveTaskStep(state)).toBe("search");
  });

  it("① 仅抓取活（scrapeSnapshot 进行态）→ search（02）", () => {
    const state = makeState({
      scrapeSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    });
    expect(liveTaskStep(state)).toBe("search");
  });

  it("① 抓取排队/暂停/失败/中断（均属未结束）→ search（02）", () => {
    for (const status of ["queued", "paused", "failed", "interrupted"]) {
      const state = makeState({
        scrapeSnapshot: ref({ status, progress: {}, logs: [] }),
      });
      expect(liveTaskStep(state)).toBe("search");
    }
  });

  it("② 仅筛选活（screenBusy / screenSnapshot 运行态）→ screen（03）", () => {
    expect(liveTaskStep(makeState({ screenBusy: ref(true) }))).toBe("screen");
    expect(liveTaskStep(makeState({
      screenSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    }))).toBe("screen");
  });

  it("② 重抓活（recrawlBusy / recrawlSnapshot 进行态）→ screen（03）", () => {
    expect(liveTaskStep(makeState({ recrawlBusy: ref(true) }))).toBe("screen");
    expect(liveTaskStep(makeState({
      recrawlSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    }))).toBe("screen");
  });

  it("② pausedRunId / interruptedRunId 存在（筛选侧未结束）→ screen（03）", () => {
    expect(liveTaskStep(makeState({ pausedRunId: ref("run-1") }))).toBe("screen");
    expect(liveTaskStep(makeState({ interruptedRunId: ref("run-2") }))).toBe("screen");
  });

  it("③ 抓取+筛选同时活 → 以真实进度为准：抓取仍活 → search；抓取已终态+筛选活 → screen", () => {
    const bothLive = makeState({
      scrapeSnapshot: ref({ status: "running", progress: {}, logs: [] }),
      screenBusy: ref(true),
    });
    expect(liveTaskStep(bothLive)).toBe("search");

    const scrapeDoneScreenLive = makeState({
      scrapeSnapshot: ref({ status: "completed", progress: {}, logs: [] }),
      screenBusy: ref(true),
    });
    expect(liveTaskStep(scrapeDoneScreenLive)).toBe("screen");
  });

  it("④ 无活任务（全部终态/空）→ 空串，不产生跳回落点", () => {
    const state = makeState({
      scrapeSnapshot: ref({ status: "completed", progress: {}, logs: [] }),
      screenSnapshot: ref({ status: "completed", progress: {}, logs: [] }),
    });
    expect(liveTaskStep(state)).toBe("");
    expect(liveTaskStep(makeState())).toBe("");
  });

  it("deriveLiveTaskStep：跨域最小判定面（useScreenRoundFlow refs 形状可直接传入）", () => {
    expect(deriveLiveTaskStep({ scrapeBusy: true })).toBe("search");
    expect(deriveLiveTaskStep({ scrapeSnapshot: { status: "paused" } })).toBe("search");
    expect(deriveLiveTaskStep({ screenSnapshot: { status: "paused" } })).toBe("screen");
    expect(deriveLiveTaskStep({ recrawlSnapshot: { status: "running" } })).toBe("screen");
    expect(deriveLiveTaskStep({})).toBe("");
  });
});

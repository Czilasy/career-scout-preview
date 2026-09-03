// 035 T002：liveTaskStep 只读派生——未结束任务的「真实进度页」统一判定面。
// 抓取活 → "search"（02）；筛选/重抓活 → "screen"（03）；无活任务 → ""。
// US2（入口守卫跳回落点）与 US3（回最新落点/按钮一致性）共用。
import { ref } from "vue";
import {
  deriveLiveTaskStep,
  liveTaskStep,
  resultCountsFromPipeline,
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

describe("resultCountsFromPipeline（036 胶囊结果提取）", () => {
  it("null 结果 → 全 0", () => {
    expect(resultCountsFromPipeline(null)).toEqual({ matched: 0, pending: 0 });
  });

  it("按 verdict 统计 matched 与 pending（待确认，与结果页 partitionPipelineResult 同源）", () => {
    // mismatch 与 uncertain 同属结果页「待确认」tab（discovery.ts partitionPipelineResult），
    // 胶囊 pending 必须与之一致（SC-009）。
    const result = {
      ok: true,
      jobs: [
        { verdict: "match" },
        { verdict: "match" },
        { verdict: "uncertain" },
        { verdict: "not_match" },
        { verdict: "mismatch" },
      ],
    } as never;
    expect(resultCountsFromPipeline(result as never)).toEqual({ matched: 2, pending: 2 });
  });

  it("无 jobs 字段 → 全 0", () => {
    expect(resultCountsFromPipeline({ ok: true } as never)).toEqual({ matched: 0, pending: 0 });
  });

  it("jobs 非数组 → 全 0", () => {
    expect(resultCountsFromPipeline({ ok: true, jobs: "bad" } as never)).toEqual({ matched: 0, pending: 0 });
  });
});

describe("roundStatusPayload 胶囊四态派生（036 FR-013 优先级）", () => {
  const jobs = [
    { verdict: "match", platform: "boss" },
    { verdict: "uncertain", platform: "boss" },
  ];

  it("空闲 → idle，平台为当前草稿平台", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    const payload = state.roundStatusPayload.value;
    expect(payload).not.toBeNull();
    expect(payload?.capsule.state).toBe("idle");
  });

  it("抓取中 → running + 进度数字", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.scrapeBusy.value = true;
    state.scrapeSnapshot.value = {
      status: "running", progress: { current: 12, total: 50 }, logs: [],
    };
    const capsule = state.roundStatusPayload.value?.capsule;
    expect(capsule?.state).toBe("running");
    if (capsule?.state === "running") {
      expect(capsule.progress.phase).toBe("scraping");
      expect(capsule.progress.done).toBe(12);
      expect(capsule.progress.total).toBe(50);
    }
  });

  // 038 复审：screen 任务内部分阶段——旧版一律落 screening，抓 JD 与
  // 真·AI 精筛显示同一文案（用户实测「抓 JD 时显示成 AI 精筛」）。
  it("038 复审：抓 JD（stage=fetch_jd）→ phase 为 jd，不再误报 AI 精筛", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.screenBusy.value = true;
    state.screenSnapshot.value = {
      status: "running", progress: { stage: "fetch_jd", current: 7, total: 20 }, logs: [],
    };
    const capsule = state.roundStatusPayload.value?.capsule;
    expect(capsule?.state).toBe("running");
    if (capsule?.state === "running") {
      expect(capsule.progress.phase).toBe("jd");
      expect(capsule.progress.done).toBe(7);
      expect(capsule.progress.total).toBe(20);
    }
  });

  it("038 复审：AI 精筛（stage=screen_b）→ phase 仍为 screening", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.screenBusy.value = true;
    state.screenSnapshot.value = {
      status: "running", progress: { stage: "screen_b", current: 3, total: 20 }, logs: [],
    };
    const capsule = state.roundStatusPayload.value?.capsule;
    if (capsule?.state === "running") {
      expect(capsule.progress.phase).toBe("screening");
    }
  });

  it("筛选完成有结果 → completed + 结果数字", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.resultLoaded.value = true;
    state.pipelineResult.value = { ok: true, jobs } as never;
    const capsule = state.roundStatusPayload.value?.capsule;
    expect(capsule?.state).toBe("completed");
    if (capsule?.state === "completed") {
      expect(capsule.results.matched).toBe(1);
      expect(capsule.results.pending).toBe(1);
    }
  });

  it("暂停 → attention（优先级高于运行/结果）", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.pausedRunId.value = "run-1";
    state.resultLoaded.value = true;
    state.pipelineResult.value = { ok: true, jobs } as never;
    state.scrapeBusy.value = true;
    const capsule = state.roundStatusPayload.value?.capsule;
    expect(capsule?.state).toBe("attention");
    if (capsule?.state === "attention") {
      expect(capsule.attention.kind).toBe("paused");
    }
  });

  it("失败 → attention error", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.screenSnapshot.value = {
      status: "failed", progress: {}, logs: [], error: "boom",
    };
    const capsule = state.roundStatusPayload.value?.capsule;
    expect(capsule?.state).toBe("attention");
    if (capsule?.state === "attention") {
      expect(capsule.attention.kind).toBe("error");
      expect(capsule.attention.message).toBe("boom");
    }
  });
});

// 035 T002：liveTaskStep 只读派生——未结束任务的「真实进度页」统一判定面。
// 抓取活 → "search"（02）；筛选/重抓活 → "screen"（03）；无活任务 → ""。
// US2（入口守卫跳回落点）与 US3（回最新落点/按钮一致性）共用。
import { reactive, ref } from "vue";
import {
  deriveLiveTaskStep,
  hasLiveTaskState,
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

  it("① 抓取排队/暂停 → search（02），错误/中断快照不再算活任务", () => {
    for (const status of ["queued", "paused"]) {
      const state = makeState({
        scrapeSnapshot: ref({ status, progress: {}, logs: [] }),
      });
      expect(liveTaskStep(state)).toBe("search");
    }

    for (const status of ["failed", "interrupted"]) {
      const state = makeState({
        scrapeSnapshot: ref({ status, progress: {}, logs: [] }),
      });
      expect(liveTaskStep(state)).toBe("");
      expect(state.pipelineBusy.value).toBe(false);
      expect(hasLiveTaskState(state)).toBe(false);
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

  it("② pausedRunId 存在（筛选侧未结束）→ screen（03）", () => {
    expect(liveTaskStep(makeState({ pausedRunId: ref("run-1") }))).toBe("screen");
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

  // 037 复审：screen 任务内部分阶段——旧版一律落 screening，抓 JD 与
  // 真·AI 精筛显示同一文案（用户实测「抓 JD 时显示成 AI 精筛」）。
  it("037 复审：抓 JD（stage=fetch_jd）→ phase 为 jd，不再误报 AI 精筛", () => {
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

  it("037 修订：补抓 JD（recrawl_fetch_jd）优先使用补抓快照并显示 jd", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.scrapeSnapshot.value = {
      status: "completed", progress: { current: 99, total: 99 }, logs: [],
    };
    state.recrawlBusy.value = true;
    state.recrawlSnapshot.value = {
      status: "running", progress: { stage: "recrawl_fetch_jd", current: 7, total: 20 }, logs: [],
    };

    const capsule = state.roundStatusPayload.value?.capsule;
    expect(capsule?.state).toBe("running");
    if (capsule?.state === "running") {
      expect(capsule.progress.phase).toBe("jd");
      expect(capsule.progress.done).toBe(7);
      expect(capsule.progress.total).toBe(20);
    }
  });

  it("037 复审：AI 精筛（stage=screen_b）→ phase 仍为 screening", () => {
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

  it("033 V2：结果完整性失败/无法确认 → attention，不按岗位数显示完成", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.resultLoaded.value = true;
    state.pipelineResult.value = {
      ok: true,
      jobs: [{ verdict: "match" }],
      integrity: {
        conclusion: "unverifiable", label: "无法确认", degraded: false,
        evidence_complete: false, primary_code: "unit_evidence_missing",
        primary_reason: "证据不足", recommendation: "建议重新执行", revision: 2,
      },
    } as never;
    const payload = state.roundStatusPayload.value;
    expect(payload?.capsule.state).toBe("attention");
    expect(payload?.integrity?.conclusion).toBe("unverifiable");
  });

  it("033 V2：部分完成保留结果胶囊但携带同一完整性结论", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.resultLoaded.value = true;
    state.pipelineResult.value = {
      ok: false,
      jobs: [{ verdict: "match" }],
      integrity: {
        conclusion: "partial", label: "部分完成", degraded: false,
        evidence_complete: true, primary_code: "unit_failed",
        primary_reason: "一组失败", recommendation: "查看已有结果或重试缺失部分", revision: 3,
      },
    } as never;
    const payload = state.roundStatusPayload.value;
    expect(payload?.capsule.state).toBe("completed");
    expect(payload?.integrity?.conclusion).toBe("partial");
  });

  it("Spec041 后续：用户主动结束保存的轮次不按「中断」展示（灵动岛落回结果页）", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.resultLoaded.value = true;
    state.finishedPartial.value = true;
    state.pipelineResult.value = {
      ok: true,
      jobs: [{ verdict: "match" }],
      integrity: {
        conclusion: "interrupted", label: "已中断", degraded: false,
        evidence_complete: false, primary_code: "interrupted",
        primary_reason: "任务因取消或停止而中断", recommendation: "", revision: 4,
      },
    } as never;

    const payload = state.roundStatusPayload.value;

    expect(payload?.capsule.state).toBe("completed");
    expect(payload?.integrity?.conclusion).toBe("partial");
    expect(payload?.integrity?.primary_reason).toBe("已结束保存部分结果");
  });

  it("Spec041 后续：不是用户收尾的中断仍按异常展示", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.resultLoaded.value = true;
    state.finishedPartial.value = false;
    state.pipelineResult.value = {
      ok: true,
      jobs: [{ verdict: "match" }],
      integrity: {
        conclusion: "interrupted", label: "已中断", degraded: false,
        evidence_complete: false, primary_code: "interrupted",
        primary_reason: "任务因取消或停止而中断", recommendation: "", revision: 4,
      },
    } as never;

    const payload = state.roundStatusPayload.value;

    expect(payload?.capsule.state).toBe("attention");
    expect(payload?.integrity?.conclusion).toBe("interrupted");
  });
});

describe("useDiscoveryState 画像切换清理", () => {
  it("切换画像时回到干净第一步并清掉旧画像结果", () => {
    const props = reactive({ profileId: "profile-old" });
    const state = useDiscoveryState(props, () => {});
    state.activeStep.value = "results";
    state.analysisReady.value = true;
    state.profileSummary.value = "旧画像";
    state.profileFacts.value = { experience: "5年" };
    state.selectedKeywords.value = ["旧关键词"];
    state.cityText.value = "旧城市";
    state.locationDraft.setLocations("boss", "旧城市", [{
      platform: "boss",
      city_name: "旧城市",
      city_code: "old-city",
      district_name: "旧区",
      district_code: "old-district",
    }]);
    state.resultLoaded.value = true;
    state.pipelineResult.value = { ok: true, jobs: [{ job_id: "old-job" }] } as never;
    state.filterValues.value = { boss: { salary: ["old"] }, zhilian: {} };
    state.workflowStateRestored.value = true;

    props.profileId = "profile-new";
    state.resetForProfileSwitch();

    expect(state.activeStep.value).toBe("upload");
    expect(state.analysisReady.value).toBe(false);
    expect(state.profileSummary.value).toBe("");
    expect(state.profileFacts.value).toEqual({});
    expect(state.selectedKeywords.value).toEqual([]);
    expect(state.cityText.value).toBe("");
    expect(state.locationDraft.getLocations("boss", "旧城市")).toEqual([]);
    props.profileId = "profile-old";
    expect(state.locationDraft.getLocations("boss", "旧城市")).toHaveLength(1);
    expect(state.resultLoaded.value).toBe(false);
    expect(state.pipelineResult.value).toBeNull();
    expect(state.filterValues.value).toEqual({ boss: {}, zhilian: {} });
    expect(state.workflowStateRestored.value).toBe(false);
  });
});

describe("useDiscoveryState.enabledSteps 步骤可达（Spec041 返工补丁）", () => {
  it("切平台后本轮抓取身份已清、结果仍在展示 → 第 3 步不再锁死", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.analysisReady.value = true;
    // 切平台后的真实现场：本轮抓取标记被清掉，但上一轮结果继续展示。
    state.scrapeCompleted.value = false;
    state.resultLoaded.value = true;
    expect(state.enabledSteps.value).toEqual(["upload", "search", "screen", "results"]);
  });

  it("既没有抓取完成也没有结果时，第 3 步仍不可进", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.analysisReady.value = true;
    expect(state.enabledSteps.value).toEqual(["upload", "search"]);
  });
});

describe("useDiscoveryState 城市草稿（「全国」不是城市）", () => {
  it("草稿残留「全国」→ 城市列表为空、执行口径仍是全国范围", () => {
    const state = makeState();
    state.cityText.value = "全国";
    expect(state.cityList.value).toEqual([]);
    expect(state.effectiveSearchCities.value).toEqual(["全国"]);
  });

  it("真实城市与「全国」混写 → 只保留真实城市", () => {
    const state = makeState();
    state.cityText.value = "上海，全国";
    expect(state.cityList.value).toEqual(["上海"]);
  });
});

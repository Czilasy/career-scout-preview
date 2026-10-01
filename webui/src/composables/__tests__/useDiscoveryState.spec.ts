// 035 T002：liveTaskStep 只读派生——未结束任务的「真实进度页」统一判定面。
// 抓取活 → "search"（02）；筛选/重抓活 → "screen"（03）；无活任务 → ""。
// US2（入口守卫跳回落点）与 US3（回最新落点/按钮一致性）共用。
import { reactive, ref } from "vue";
import {
  deriveLiveTaskStep,
  hasLiveTaskState,
  hasUnfinishedRound,
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
      // 用户主动暂停（可恢复）仍是「已暂停」，这条口径不许被中断文案带跑。
      expect(capsule.attention.message).toContain("已暂停");
    }
    expect(state.roundStatusPayload.value?.stuckAt).toBe("scrape");
  });

  // SPEC 046 第五轮：服务重启打断的轮次，03 页 AI 筛选卡写「已中断」、02 页抓取卡写
  // 「完整成功」，而顶栏灵动岛说「任务已暂停，请处理后继续」——岛谎报了状态性质。
  // SPEC 046 D-06（状态词表）：中断没有活体 worker，轨道级「继续」在服务重启后必然
  // 失败，界面上也不存在这个入口；中断的唯一出路是开新一轮。文案不得再承诺「继续」。
  it("筛选被服务重启打断 → 岛上说已中断，不再谎报已暂停", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    const payload = state.roundStatusPayload.value;
    const capsule = payload?.capsule;
    expect(capsule?.state).toBe("attention");
    if (capsule?.state === "attention") {
      // kind 仍走既有 paused 通道：胶囊状态枚举与导航落点都不因文案而变。
      expect(capsule.attention.kind).toBe("paused");
      expect(capsule.attention.message).toContain("已中断");
      expect(capsule.attention.message).not.toContain("已暂停");
      expect(capsule.attention.message).toContain("开始新一轮");
      expect(capsule.attention.message).not.toContain("继续");
    }
    expect(payload?.stuckAt).toBe("screen");
  });

  it("用户主动暂停仍然说「处理后继续」：只有中断才指向新一轮", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.pausedRunId.value = "run-paused-copy";
    const capsule = state.roundStatusPayload.value?.capsule;
    expect(capsule?.state).toBe("attention");
    if (capsule?.state === "attention") {
      expect(capsule.attention.message).toContain("已暂停");
      expect(capsule.attention.message).toContain("继续");
    }
  });

  it("只剩中断断点（interruptedRunId）→ 岛同样说已中断", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.interruptedRunId.value = "interrupted-run-1";
    const payload = state.roundStatusPayload.value;
    const capsule = payload?.capsule;
    expect(capsule?.state).toBe("attention");
    if (capsule?.state === "attention") {
      expect(capsule.attention.kind).toBe("paused");
      expect(capsule.attention.message).toContain("已中断");
      expect(capsule.attention.message).not.toContain("已暂停");
    }
    expect(payload?.stuckAt).toBe("scrape");
  });

  it("抓取被服务重启打断 → 岛说已中断且落点仍是抓取页", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.scrapeSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    const payload = state.roundStatusPayload.value;
    const capsule = payload?.capsule;
    expect(capsule?.state).toBe("attention");
    if (capsule?.state === "attention") {
      expect(capsule.attention.kind).toBe("paused");
      expect(capsule.attention.message).toContain("已中断");
      expect(capsule.attention.message).not.toContain("已暂停");
    }
    expect(payload?.stuckAt).toBe("scrape");
  });

  // SPEC 046 第五轮（通知行）：暂停族只有一条 kind 通道，展开面板的行标题必须知道
  // 这一轮到底是「可恢复的暂停」还是「被服务重启打断」——性质由这里（唯一判定面）
  // 一并交给下游，展示端不再判第二次，否则同一行的标题与详情两个说法。
  it("暂停族把性质一并交给下游：快照中断与中断断点都给 interrupted", () => {
    const interruptedScreen = useDiscoveryState({ profileId: "test" }, () => {});
    interruptedScreen.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    interruptedScreen.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    const screenCapsule = interruptedScreen.roundStatusPayload.value?.capsule;
    expect(screenCapsule?.state).toBe("attention");
    if (screenCapsule?.state === "attention") {
      expect(screenCapsule.attention.pausedFact).toBe("interrupted");
    }

    const interruptedRun = useDiscoveryState({ profileId: "test" }, () => {});
    interruptedRun.interruptedRunId.value = "interrupted-run-1";
    const runCapsule = interruptedRun.roundStatusPayload.value?.capsule;
    expect(runCapsule?.state).toBe("attention");
    if (runCapsule?.state === "attention") {
      expect(runCapsule.attention.pausedFact).toBe("interrupted");
    }
  });

  it("暂停族把性质一并交给下游：可恢复暂停给 paused，kind 与落点不变", () => {
    const state = useDiscoveryState({ profileId: "test" }, () => {});
    state.pausedRunId.value = "run-paused";
    const payload = state.roundStatusPayload.value;
    const capsule = payload?.capsule;
    expect(capsule?.state).toBe("attention");
    if (capsule?.state === "attention") {
      expect(capsule.attention.kind).toBe("paused");
      expect(capsule.attention.pausedFact).toBe("paused");
    }
    expect(payload?.stuckAt).toBe("scrape");
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

describe("useDiscoveryState 统一导航守卫", () => {
  it("活动 Flow Track 时锁住一键入口和平台范围", () => {
    const state = useDiscoveryState({ profileId: "navigation-flow-active" }, () => {});
    const navigation = state as typeof state & {
      setFlowActive: (active: boolean) => void;
    };

    navigation.setFlowActive(true);

    expect(state.oneClickDisabled.value).toBe(true);
    expect(state.scopeLocked.value).toBe(true);
  });

  it("全部平台的 Flow 可达投影放行普通点击，即使旧平台集合尚未解锁", () => {
    const state = useDiscoveryState({ profileId: "navigation-flow" }, () => {});
    state.analysisReady.value = true;
    const navigation = state as typeof state & {
      setFlowReachableSteps: (steps: Set<string> | null) => void;
      navigateStep: (step: string) => string;
    };

    navigation.setFlowReachableSteps(new Set(["search", "screen"]));
    navigation.navigateStep("screen");

    expect(state.enabledSteps.value).toEqual(["upload", "search", "screen"]);
    expect(state.activeStep.value).toBe("screen");
  });

  it("恢复后主动校正不可达页，重复校正保持同一落点", () => {
    const state = useDiscoveryState({ profileId: "navigation-reconcile" }, () => {});
    const navigation = state as typeof state & {
      reconcileActiveStep: (step?: string) => string;
    };

    state.activeStep.value = "results";
    expect(navigation.reconcileActiveStep()).toBe("upload");
    expect(state.activeStep.value).toBe("upload");
    expect(navigation.reconcileActiveStep()).toBe("upload");
    expect(state.activeStep.value).toBe("upload");
  });

  it("画像切换重置导航投影与人工停留态", () => {
    const state = useDiscoveryState({ profileId: "navigation-reset" }, () => {});
    const navigation = state as typeof state & {
      setFlowReachableSteps: (steps: Set<string> | null) => void;
      setNavigationManualHold: (hold: boolean) => void;
    };

    navigation.setFlowReachableSteps(new Set(["search", "screen", "results"]));
    navigation.setNavigationManualHold(true);
    state.resetForProfileSwitch();

    expect(state.enabledSteps.value).toEqual(["upload"]);
    expect(state.activeStep.value).toBe("upload");
    expect((navigation as typeof navigation & { navigationManualHold: { value: boolean } }).navigationManualHold.value).toBe(false);
  });

  it("人工停留态统一拦截 Flow 与系统自动跳页，但保留用户点击和恢复校正", () => {
    const state = useDiscoveryState({ profileId: "navigation-manual-hold" }, () => {});
    state.analysisReady.value = true;
    state.scrapeCompleted.value = true;
    state.resultLoaded.value = true;
    state.activeStep.value = "search";
    const navigation = state as typeof state & {
      setNavigationManualHold: (hold: boolean) => void;
      navigateStep: (step: string, options?: { source?: "user" | "flow" | "restore" | "system" }) => string;
      reconcileActiveStep: (step?: string) => string;
    };

    navigation.setNavigationManualHold(true);

    expect(navigation.navigateStep("results", { source: "flow" })).toBe("search");
    expect(navigation.navigateStep("results", { source: "system" })).toBe("search");
    expect(navigation.reconcileActiveStep("results")).toBe("results");
    expect(navigation.navigateStep("search", { source: "user" })).toBe("search");
  });
});

// 平台切换被禁用时的提示必须说真话：之前不分原因一律写「任务进行中，平台已锁定」，
// 流程早已终态、什么都没在跑，顶部平台切换仍被锁着（因为已在第 3/4 步或在看历史轮），
// 用户被告知有一个并不存在的任务。锁定本身不放宽，只把原因写对。
describe("useDiscoveryState.scopeLockReason（禁用原因如实说明）", () => {
  type ReasonState = ReturnType<typeof useDiscoveryState> & {
    setFlowActive: (active: boolean) => void;
  };

  it("未锁定 → 不给任何提示", () => {
    const state = useDiscoveryState({ profileId: "lock-none" }, () => {}) as ReasonState;
    expect(state.scopeLocked.value).toBe(false);
    expect(state.scopeLockReason.value).toBe("");
  });

  it("确有活动 Flow 轨道 → 才说任务进行中", () => {
    const state = useDiscoveryState({ profileId: "lock-flow" }, () => {}) as ReasonState;
    // 外壳「已中断」仍算活动线：本轮范围锁死是正确事实，解锁的只有「开始新一轮」。
    state.setFlowActive(true);
    expect(state.scopeLocked.value).toBe(true);
    expect(state.scopeLockReason.value).toBe("任务进行中，平台已锁定");
  });

  it("有暂停待处理的轮次 → 说已暂停，不说进行中", () => {
    const state = useDiscoveryState({ profileId: "lock-paused" }, () => {}) as ReasonState;
    state.pausedRunId.value = "run-paused";
    expect(state.scopeLocked.value).toBe(true);
    expect(state.scopeLockReason.value).toBe("任务已暂停，平台已锁定");
  });

  it("查看历史轮次 → 说历史轮次，不谎称任务进行中", () => {
    const state = useDiscoveryState({ profileId: "lock-history" }, () => {}) as ReasonState;
    state.historyRound.value = { runId: "h1", platform: "boss", status: "done", jobCount: 3 };
    expect(state.scopeLocked.value).toBe(true);
    expect(state.scopeLockReason.value).toContain("历史轮次");
    expect(state.scopeLockReason.value).not.toContain("任务进行中");
  });

  it("只是停在第 4 步、没有任何任务 → 说范围已确认，不谎称任务进行中", () => {
    const state = useDiscoveryState({ profileId: "lock-results" }, () => {}) as ReasonState;
    state.activeStep.value = "results";
    expect(state.scopeLocked.value).toBe(true);
    expect(state.scopeLockReason.value).not.toContain("任务进行中");
    expect(state.scopeLockReason.value).toContain("平台已锁定");
  });
});

// SPEC 046 第五轮：判活只允许一份口径，落在树干；落点跟随流程投影。
// 真实现场——并行流程的活动线由协调器投影成 flowActive，本地三个任务快照仍是上一轮
// 终态、暂停轮次为空：树干 hasLiveTaskState 认这个活，落点派生此前不认，于是
// 「有活任务」与「没有落点」同时成立，上传简历入口守卫把用户带进开新一轮路径。
// 落点跟随的是 Flow 投影到 state 的阶段集合（flowReachableSteps），不是被 historyMode
// 收窄后的 enabledSteps。
describe("useDiscoveryState 判活与落点同源（SPEC 046 第五轮）", () => {
  // useDiscoveryState 在函数体内声明全部 ref（:91 起），每个实例各自独立：
  // 判活用到的事实（流程活动线、任务快照、暂停轮次）没有跨实例通道，
  // 每个用例直接造自己的现场即可，不需要任何"清场"装置。
  function makeFlowOnlyState(profileId: string): DiscoveryState {
    const state = useDiscoveryState({ profileId }, () => {});
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenSnapshot.value = { status: "failed", progress: {}, logs: [] };
    state.setFlowActive(true);
    return state;
  }

  it("① 只因流程活动线而活：树干判到活时落点不得为空，且落点在可达清单里", () => {
    const state = makeFlowOnlyState("same-source-landing");
    expect(hasLiveTaskState(state)).toBe(true);
    const step = liveTaskStep(state);
    expect(step).not.toBe("");
    expect(state.enabledSteps.value).toContain(step);
  });

  it("② 落点跟随流程投影的进行中阶段，取最深的一支（screen 优先于 search）", () => {
    const state = makeFlowOnlyState("same-source-projection");
    // 投影还没到达：流程活动线的最小开放面只有 02，落点不凭空造 03。
    expect(liveTaskStep(state)).toBe("search");
    state.setFlowReachableSteps(new Set(["search", "screen"]));
    expect(state.enabledSteps.value).toEqual(["upload", "search", "screen"]);
    // 真实现场：并行流程抓取已完成、AI 筛选正在跑。02 页只渲染抓取列表，
    // 落 02 就看不到 03 正在跑的筛选进度，落点必须是最深的那一支。
    expect(liveTaskStep(state)).toBe("screen");
    // 投影只开到 02（抓取仍在跑）时仍然落 02。
    state.setFlowReachableSteps(new Set(["search"]));
    expect(liveTaskStep(state)).toBe("search");
  });

  it("③ 树干判到没有活任务 → 仍然不给落点，不凭空造一个", () => {
    const state = makeFlowOnlyState("same-source-idle");
    state.setFlowActive(false);
    expect(hasLiveTaskState(state)).toBe(false);
    expect(liveTaskStep(state)).toBe("");
  });

  it("④ 真实进度优先：流程活动线之上筛选确实在跑，落点仍是筛选进度页", () => {
    const state = makeFlowOnlyState("same-source-legacy");
    state.setFlowReachableSteps(new Set(["search", "screen"]));
    state.screenSnapshot.value = { status: "running", progress: {}, logs: [] };
    expect(liveTaskStep(state)).toBe("screen");
    state.screenSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.scrapeSnapshot.value = { status: "queued", progress: {}, logs: [] };
    expect(liveTaskStep(state)).toBe("search");
  });

  it("⑤ 正在看历史轮（enabledSteps 被收窄成 04）：落点仍跟随投影，不造不可达步骤", () => {
    const state = makeFlowOnlyState("same-source-history");
    state.setFlowReachableSteps(new Set(["search", "screen"]));
    state.historyRound.value = { runId: "h1", platform: "boss", status: "done", jobCount: 1 };
    // 「回到最新」在清掉历史轮之前求值落点：此刻 enabledSteps 只剩 04，
    // 拿它当判定面会把"有活任务就不去请求最新结果"的守卫判反。
    expect(state.enabledSteps.value).toEqual(["results"]);
    expect(hasLiveTaskState(state)).toBe(true);
    expect(liveTaskStep(state)).toBe("screen");
  });

  it("⑥ 投影里没有任何进行中阶段 → 不给落点，结果页不当进度页", () => {
    const state = makeFlowOnlyState("same-source-results-only");
    state.setFlowReachableSteps(new Set(["results"]));
    expect(state.enabledSteps.value).toContain("results");
    expect(liveTaskStep(state)).toBe("");
  });

  // SPEC 046 收尾守卫：判活（问题 A「此刻有没有活体 worker」）与未结束（问题 B
  // 「这一轮还没结束」）分派。轨道排队中/运行中 → 页面把 hasLiveWorker 投影成活体 →
  // 判活为真（04 因此不接本轮结果，见 DiscoveryRecovery「仍有活体轨道时 04 不接本轮结果」）；
  // 已中断 → 没活体但也没结束 → 判活为假、落点照旧跟随投影、范围与提交仍然锁死。
  it.each([true, false])("⑦ 活体与未结束分派（页面投影活体轨道=%s）", (liveWorker) => {
    const state = makeFlowOnlyState(`same-source-dispatch-${liveWorker}`);
    state.setFlowReachableSteps(new Set(["search", "screen"]));
    state.setFlowLiveWorker(liveWorker);
    expect(hasLiveTaskState(state)).toBe(liveWorker);
    // 两者都属于「这一轮还没结束」：范围与提交守卫不因判活放开而放松。
    expect(hasUnfinishedRound(state)).toBe(true);
    expect(state.scopeLocked.value).toBe(true);
    expect(state.pipelineBusy.value).toBe(true);
    // 落点问的是未结束，不是活体：已中断的轮也必须有一个可达的真实进度页。
    expect(liveTaskStep(state)).toBe("screen");
  });
});

// 035 T002：unfinishedRoundStep 只读派生——未结束任务的「真实进度页」统一判定面。
// 抓取活 → "search"（02）；筛选/重抓活 → "screen"（03）；无活任务 → ""。
// US2（入口守卫跳回落点）与 US3（回最新落点/按钮一致性）共用。
import { reactive, ref } from "vue";
import {
  deriveUnfinishedRoundStep,
  flowProblemFallbackMessage,
  hasLiveTaskState,
  hasUnfinishedRound,
  unfinishedRoundStep,
  resultCountsFromPipeline,
  useDiscoveryState,
} from "../useDiscoveryState";
import type { DiscoveryState } from "../useDiscoveryState";

function makeState(overrides: Partial<DiscoveryState> = {}): DiscoveryState {
  const state = useDiscoveryState({ profileId: "test" }, () => {});
  return Object.assign(state, overrides);
}

describe("useDiscoveryState.unfinishedRoundStep（035 真实进度页派生）", () => {
  it("① 仅抓取活（scrapeBusy）→ search（02）", () => {
    const state = makeState({ scrapeBusy: ref(true) });
    expect(unfinishedRoundStep(state)).toBe("search");
  });

  it("① 仅抓取活（scrapeSnapshot 进行态）→ search（02）", () => {
    const state = makeState({
      scrapeSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    });
    expect(unfinishedRoundStep(state)).toBe("search");
  });

  it("① 抓取排队/暂停 → search（02），错误/中断快照不再算活任务", () => {
    for (const status of ["queued", "paused"]) {
      const state = makeState({
        scrapeSnapshot: ref({ status, progress: {}, logs: [] }),
      });
      expect(unfinishedRoundStep(state)).toBe("search");
    }

    for (const status of ["failed", "interrupted"]) {
      const state = makeState({
        scrapeSnapshot: ref({ status, progress: {}, logs: [] }),
      });
      expect(unfinishedRoundStep(state)).toBe("");
      expect(state.pipelineBusy.value).toBe(false);
      expect(hasLiveTaskState(state)).toBe(false);
    }
  });

  it("② 仅筛选活（screenBusy / screenSnapshot 运行态）→ screen（03）", () => {
    expect(unfinishedRoundStep(makeState({ screenBusy: ref(true) }))).toBe("screen");
    expect(unfinishedRoundStep(makeState({
      screenSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    }))).toBe("screen");
  });

  it("② 重抓活（recrawlBusy / recrawlSnapshot 进行态）→ screen（03）", () => {
    expect(unfinishedRoundStep(makeState({ recrawlBusy: ref(true) }))).toBe("screen");
    expect(unfinishedRoundStep(makeState({
      recrawlSnapshot: ref({ status: "running", progress: {}, logs: [] }),
    }))).toBe("screen");
  });

  it("② pausedRunId 存在（筛选侧未结束）→ screen（03）", () => {
    expect(unfinishedRoundStep(makeState({ pausedRunId: ref("run-1") }))).toBe("screen");
  });

  it("③ 抓取+筛选同时活 → 以真实进度为准：抓取仍活 → search；抓取已终态+筛选活 → screen", () => {
    const bothLive = makeState({
      scrapeSnapshot: ref({ status: "running", progress: {}, logs: [] }),
      screenBusy: ref(true),
    });
    expect(unfinishedRoundStep(bothLive)).toBe("search");

    const scrapeDoneScreenLive = makeState({
      scrapeSnapshot: ref({ status: "completed", progress: {}, logs: [] }),
      screenBusy: ref(true),
    });
    expect(unfinishedRoundStep(scrapeDoneScreenLive)).toBe("screen");
  });

  it("④ 无活任务（全部终态/空）→ 空串，不产生跳回落点", () => {
    const state = makeState({
      scrapeSnapshot: ref({ status: "completed", progress: {}, logs: [] }),
      screenSnapshot: ref({ status: "completed", progress: {}, logs: [] }),
    });
    expect(unfinishedRoundStep(state)).toBe("");
    expect(unfinishedRoundStep(makeState())).toBe("");
  });

  it("deriveUnfinishedRoundStep：跨域最小判定面（useScreenRoundFlow refs 形状可直接传入）", () => {
    expect(deriveUnfinishedRoundStep({ scrapeBusy: true })).toBe("search");
    expect(deriveUnfinishedRoundStep({ scrapeSnapshot: { status: "paused" } })).toBe("search");
    expect(deriveUnfinishedRoundStep({ screenSnapshot: { status: "paused" } })).toBe("screen");
    expect(deriveUnfinishedRoundStep({ recrawlSnapshot: { status: "running" } })).toBe("screen");
    expect(deriveUnfinishedRoundStep({})).toBe("");
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
    // 真实的服务重启形态：协调器把 Flow 投影（flows + flow_tracks 里 interrupted 的轨道）
    // 投成 flowActive——中断告警的依据是轮次事实，任务快照只负责把错误留在页面上。
    state.flowActive.value = true;
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
    state.flowActive.value = true;
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
    state.flowActive.value = true;
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
    interruptedScreen.flowActive.value = true;
    interruptedScreen.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    interruptedScreen.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    const screenCapsule = interruptedScreen.roundStatusPayload.value?.capsule;
    expect(screenCapsule?.state).toBe("attention");
    if (screenCapsule?.state === "attention") {
      expect(screenCapsule.attention.pausedFact).toBe("interrupted");
    }

    const interruptedRun = useDiscoveryState({ profileId: "test" }, () => {});
    interruptedRun.flowActive.value = true;
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

  // SPEC 046 D-09（状态词表 + 状态所有权）：冷启动走查实测——最新一轮的两条轨道都是
  // done/complete，screening_runs 里一条 2026-09-29 的 zhilian run 却仍是 interrupted，
  // 恢复分支把它摆回页面，灵动岛于是对着一张已收尾的轮次喊「任务已中断，请开始新一轮」。
  // 任务快照的 interrupted 说的是「那段任务没跑完」，不是「这一轮还没结束」；后者只由
  // Flow 投影回答（树干唯一谓词 hasUnfinishedRound，读协调器投影的 flowActive 与活体/暂停事实）。
  describe("已收尾轮次残留的中断快照不再冒充轮次告警（046 D-09）", () => {
    // 现场①：轮次已终态（Flow 归属在场、Flow 投影收尾、无活体、无暂停断点）+ 残留 interrupted 快照。
    it("轮次已收尾 + 残留中断快照 → 岛不再产出中断告警，错误现场仍在快照里", () => {
      const state = useDiscoveryState({ profileId: "test" }, () => {});
      // 「有 Flow 行」由既有的归属谓词回答（hasFlowOwnership ← 协调器投影的可达集合）：
      // 这一轮确实归 Flow 管，轨道终态就是本轮已收尾，残留快照不得再冒充轮次告警。
      state.setFlowReachableSteps(new Set(["search", "screen", "results"]), "flow-closed-round");
      state.flowActive.value = false;
      state.flowLiveWorker.value = false;
      state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
      state.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [], error: "服务重启" };
      const payload = state.roundStatusPayload.value;
      expect(payload?.capsule?.state).not.toBe("attention");
      // 现场不得被抹掉：快照与任务身份仍可查错误，只是不再冒充「上一轮被中断」。
      expect(state.screenSnapshot.value?.status).toBe("interrupted");
    });

    it("轮次已收尾 + 抓取侧残留中断快照 → 同样不上岛报警", () => {
      const state = useDiscoveryState({ profileId: "test" }, () => {});
      state.setFlowReachableSteps(new Set(["search", "screen", "results"]), "flow-closed-scrape");
      state.flowActive.value = false;
      state.flowLiveWorker.value = false;
      state.scrapeSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
      expect(state.roundStatusPayload.value?.capsule?.state).not.toBe("attention");
    });

    // 现场②：轮次确实被服务重启打断（Flow 轨道 interrupted 投影成 flowActive）。
    it("轮次被服务重启打断（Flow 投影未收尾）→ 岛照旧说「已中断，请开始新一轮」", () => {
      const state = useDiscoveryState({ profileId: "test" }, () => {});
      state.flowActive.value = true;
      state.flowLiveWorker.value = false;
      state.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
      const payload = state.roundStatusPayload.value;
      const capsule = payload?.capsule;
      expect(capsule?.state).toBe("attention");
      if (capsule?.state === "attention") {
        expect(capsule.attention.kind).toBe("paused");
        expect(capsule.attention.pausedFact).toBe("interrupted");
        expect(capsule.attention.message).toContain("已中断");
        expect(capsule.attention.message).toContain("开始新一轮");
      }
      expect(payload?.stuckAt).toBe("screen");
    });

    // 现场③：用户主动暂停（paused）——文案归 paused 一族，门控不得把它一起吞掉，
    // 也不许把「处理后继续」写成「开始新一轮」。
    it("用户主动暂停 → 告警仍是「已暂停，请处理后继续」，不与中断文案混用", () => {
      const state = useDiscoveryState({ profileId: "test" }, () => {});
      state.flowActive.value = true;
      state.flowLiveWorker.value = false;
      state.screenSnapshot.value = { status: "paused", progress: {}, logs: [] };
      const capsule = state.roundStatusPayload.value?.capsule;
      expect(capsule?.state).toBe("attention");
      if (capsule?.state === "attention") {
        expect(capsule.attention.pausedFact).toBe("paused");
        expect(capsule.attention.message).toContain("已暂停");
        expect(capsule.attention.message).toContain("处理后继续");
        expect(capsule.attention.message).not.toContain("中断");
        expect(capsule.attention.message).not.toContain("开始新一轮");
      }
    });
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
      setFlowLocksNewRound: (locked: boolean) => void;
    };

    // 轨道在跑：两份投影同时到位——本轮未结束（锁范围）与开新轮闸门锁（那一份唯一清单）。
    navigation.setFlowActive(true);
    navigation.setFlowLocksNewRound(true);

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
describe("useDiscoveryState 状态词表 A/B 谓词纯度（SPEC 046 v2「状态词表」）", () => {
  // 词表唯一定义：活体任务只认排队中/运行中；已暂停、已中断属于「本轮未结束」，
  // 两个问题各有一个谓词回答，任何一侧不得把另一侧的成员并进来。
  it("① A 判活不含暂停与中断：pausedRunId 与 paused 快照都不算活体", () => {
    const state = useDiscoveryState({ profileId: "vocab-a-paused" }, () => {});
    state.pausedRunId.value = "screen-paused";
    state.screenSnapshot.value = { status: "paused", progress: {}, logs: [] };
    expect(hasLiveTaskState(state)).toBe(false);
    // 同一事实必须被 B 谓词接住：本轮未结束。
    expect(hasUnfinishedRound(state)).toBe(true);
  });

  it("② A 判活不含中断轮：Flow 投影非活体的活动线（已中断/已暂停）不算活体", () => {
    const state = useDiscoveryState({ profileId: "vocab-a-interrupted" }, () => {});
    state.setFlowActive(true);
    state.setFlowLiveWorker(false);
    expect(hasLiveTaskState(state)).toBe(false);
    expect(hasUnfinishedRound(state)).toBe(true);
  });

  it("③ B 本轮未结束包含排队/运行/暂停/中断四种，终态才解除", () => {
    for (const status of ["queued", "running", "paused", "interrupted"]) {
      const state = useDiscoveryState({ profileId: "vocab-b-members" }, () => {});
      state.setFlowActive(true);
      state.setFlowLiveWorker(status === "queued" || status === "running");
      expect(hasUnfinishedRound(state)).toBe(true);
      expect(hasLiveTaskState(state)).toBe(status === "queued" || status === "running");
    }
    const closed = useDiscoveryState({ profileId: "vocab-b-closed" }, () => {});
    closed.setFlowActive(false);
    closed.setFlowLiveWorker(false);
    expect(hasUnfinishedRound(closed)).toBe(false);
  });

  it("④ 快照排队/运行仍算活体；paused 快照属 B 不属 A；failed/cancelled 等终态两侧都不算", () => {
    const state = useDiscoveryState({ profileId: "vocab-a-snapshots" }, () => {});
    state.scrapeSnapshot.value = { status: "running", progress: {}, logs: [] };
    expect(hasLiveTaskState(state)).toBe(true);
    expect(hasUnfinishedRound(state)).toBe(true);
    state.scrapeSnapshot.value = { status: "paused", progress: {}, logs: [] };
    state.screenSnapshot.value = null;
    state.recrawlSnapshot.value = null;
    expect(hasLiveTaskState(state)).toBe(false);
    expect(hasUnfinishedRound(state)).toBe(true);
    state.scrapeSnapshot.value = { status: "cancelled", progress: {}, logs: [] };
    state.screenSnapshot.value = { status: "failed", progress: {}, logs: [] };
    expect(hasLiveTaskState(state)).toBe(false);
    expect(hasUnfinishedRound(state)).toBe(false);
  });
});

// SPEC 046 收口第一单（状态与闸门口径）：「能不能开新一轮」在整棵树里只允许有
// 一份清单，它落在 useDiscoveryParallelFlow（排队中/运行中/已暂停锁、已中断放行）；
// 树干侧的 pipelineBusy 与主启动按钮只读那份清单投影进来的一个布尔，绝不再把
// 「本轮未结束」（flowActive，含已中断）当成「开不了新一轮」。
describe("useDiscoveryState 开新轮闸门只读那一份清单（046 FR-015）", () => {
  function makeFlowScene(profileId: string, facts: {
    unfinishedRound: boolean;
    liveWorker: boolean;
    locksNewRound: boolean;
  }): DiscoveryState {
    const state = useDiscoveryState({ profileId }, () => {});
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.recrawlSnapshot.value = null;
    state.setFlowActive(facts.unfinishedRound);
    state.setFlowLiveWorker(facts.liveWorker);
    state.setFlowLocksNewRound(facts.locksNewRound);
    return state;
  }

  it("① 已中断轮：本轮范围仍锁死，但开新轮闸门放行——02 主启动按钮可点", () => {
    const state = makeFlowScene("newround-interrupted", {
      unfinishedRound: true, liveWorker: false, locksNewRound: false,
    });
    expect(state.scopeLocked.value).toBe(true);
    expect(state.pipelineBusy.value).toBe(false);
    expect(state.oneClickDisabled.value).toBe(false);
  });

  it("② 已暂停轮：主启动按钮锁住，锁定原因说已暂停", () => {
    const state = makeFlowScene("newround-paused", {
      unfinishedRound: true, liveWorker: false, locksNewRound: true,
    });
    expect(state.pipelineBusy.value).toBe(true);
    expect(state.oneClickDisabled.value).toBe(true);
    expect(state.scopeLockReason.value).toBe("任务已暂停，平台已锁定");
  });

  it("③ 运行中的轮：主启动按钮锁住，锁定原因说任务进行中", () => {
    const state = makeFlowScene("newround-running", {
      unfinishedRound: true, liveWorker: true, locksNewRound: true,
    });
    expect(state.pipelineBusy.value).toBe(true);
    expect(state.oneClickDisabled.value).toBe(true);
    expect(state.scopeLockReason.value).toBe("任务进行中，平台已锁定");
  });

  it("④ 树干不留第二份清单：活动线单独在场不再顺带锁住开新轮闸门", () => {
    // 活动线（B）不是开新轮闸门（那份清单）的成员来源：未经该清单投影的调用方
    // 只能得出「闸门没锁」，不许再从 B 猜一份成员不同的清单。
    const state = useDiscoveryState({ profileId: "newround-no-second-list" }, () => {});
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    state.setFlowActive(true);
    expect(state.pipelineBusy.value).toBe(false);
    expect(state.scopeLocked.value).toBe(true);
  });

  // 状态词表硬要求：「有没有活体任务」与「这一轮完没完结」各有一份谓词，任何一侧
  // 不得向另一侧借道。投影缺席（flowLiveWorker 仍为 null）时没有「此刻无活体」这条
  // 否定证据，也没有「有活体」这条肯定证据——只认快照自己的 running/queued。
  it("⑤ A 谓词不在投影缺席时向 B 借道：活动线在场、快照全终态 → 没有活体", () => {
    const state = useDiscoveryState({ profileId: "vocab-a-no-borrow" }, () => {});
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    state.recrawlSnapshot.value = null;
    state.setFlowActive(true);
    expect(hasLiveTaskState(state)).toBe(false);
    // 同一份事实由 B 接住：本轮未结束（含中断轮）。
    expect(hasUnfinishedRound(state)).toBe(true);
  });

  it("⑥ A 谓词在投影缺席时仍认快照自己的排队/运行", () => {
    const state = useDiscoveryState({ profileId: "vocab-a-snapshot-only" }, () => {});
    state.screenSnapshot.value = { status: "queued", progress: {}, logs: [] };
    expect(hasLiveTaskState(state)).toBe(true);
  });

  // 上一单把「轮次已收尾」的中断告警按 B 分层，但 B 在没有 flows 行的 pre-046 旧轮次上
  // 恒为 false——真被服务重启打断的 legacy 轮因此再也没人提示。B 必须自持 legacy 一支：
  // 没有 Flow 归属时，该轮自己的 run/task 中断事实就是「本轮未结束」。
  it("⑦ 没有 Flow 归属的旧轮次带中断快照 → B 仍说本轮未结束", () => {
    const state = useDiscoveryState({ profileId: "legacy-b-interrupted-snapshot" }, () => {});
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    expect(state.flowOwnership.value).toBe(false);
    expect(hasUnfinishedRound(state)).toBe(true);
  });

  it("⑧ 没有 Flow 归属的旧轮次只剩中断断点 → B 同样说本轮未结束", () => {
    const state = useDiscoveryState({ profileId: "legacy-b-interrupted-run" }, () => {});
    state.interruptedRunId.value = "screen-legacy-interrupted";
    expect(state.flowOwnership.value).toBe(false);
    expect(hasUnfinishedRound(state)).toBe(true);
  });

  it("⑨ 有 Flow 归属且轨道终态 → 残留中断快照不算本轮未结束（守住 D-09 不回退）", () => {
    const state = useDiscoveryState({ profileId: "legacy-b-flow-closed" }, () => {});
    state.setFlowReachableSteps(new Set(["search", "screen", "results"]), "flow-closed-b");
    state.setFlowActive(false);
    state.setFlowLiveWorker(false);
    state.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    state.interruptedRunId.value = "screen-legacy-interrupted";
    expect(state.flowOwnership.value).toBe(true);
    expect(hasUnfinishedRound(state)).toBe(false);
  });

  it("⑩ 没有 Flow 归属的旧中断轮 → 岛照旧给「已中断，请开始新一轮」", () => {
    const state = useDiscoveryState({ profileId: "legacy-b-island" }, () => {});
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    const capsule = state.roundStatusPayload.value?.capsule;
    expect(capsule?.state).toBe("attention");
    if (capsule?.state === "attention" && capsule.attention.kind === "paused") {
      expect(capsule.attention.pausedFact).toBe("interrupted");
      expect(capsule.attention.message).toContain("已中断");
      expect(capsule.attention.message).toContain("开始新一轮");
      expect(capsule.attention.message).not.toContain("处理后继续");
    }
  });

  // legacy 一支只回答「这一轮到底完没完结」（告警与轮次归属），不凭空造一个进度页：
  // 没有 Flow 归属、也没有活体/暂停现场的旧轮，落点必须为空——否则「开始新一轮」这条
  // 唯一出路会被入口守卫钉死在旧轮上（状态词表硬要求：出口不得以"有活体任务"为唯一判据）。
  it("⑫ 没有 Flow 归属的旧中断轮 → 不给落点，开始新一轮的出路不被钉死", () => {
    const state = useDiscoveryState({ profileId: "legacy-b-landing" }, () => {});
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenSnapshot.value = { status: "interrupted", progress: {}, logs: [] };
    expect(hasUnfinishedRound(state)).toBe(true);
    expect(unfinishedRoundStep(state)).toBe("");
    // 同一份现场里若有活体或暂停断点，落点照旧来自既有任务事实，不依赖本轮新增的一支。
    state.screenSnapshot.value = { status: "paused", progress: {}, logs: [] };
    expect(unfinishedRoundStep(state)).toBe("screen");
  });

  // 状态词表：「继续」只对用户主动暂停成立，中断没有活体 worker、界面上没有继续入口。
  // 轨道问题的兜底文案必须按这条事实分层取词，不许中断与暂停共用一句「请处理后继续」。
  it("⑪ 轨道问题兜底按事实分层：中断与终态说请开始新一轮，暂停才说请处理后继续", () => {
    expect(flowProblemFallbackMessage("interrupted")).toBe("AI 筛选未完成，请开始新一轮");
    expect(flowProblemFallbackMessage("cancelled")).toBe("AI 筛选未完成，请开始新一轮");
    expect(flowProblemFallbackMessage("failed")).toBe("AI 筛选未完成，请开始新一轮");
    expect(flowProblemFallbackMessage("paused")).toBe("AI 筛选未完成，请处理后继续");
    expect(flowProblemFallbackMessage("done")).toBe("AI 筛选未完成，请开始新一轮");

    const state = useDiscoveryState({ profileId: "flow-problem-fallback" }, () => {});
    state.pipelineResult.value = {
      jobs: [], dropped: [],
      flow_id: "flow-incomplete",
      flow_tracks: [
        { platform: "boss", status: "done", stage: "complete", result_run_id: "run-b" },
        { platform: "zhilian", status: "cancelled", stage: "ai", unfinished_ai_screening: true },
      ],
    } as never;
    const capsule = state.roundStatusPayload.value?.capsule;
    expect(capsule?.state).toBe("attention");
    if (capsule?.state === "attention" && capsule.attention.kind === "error") {
      expect(capsule.attention.message).toContain("开始新一轮");
      expect(capsule.attention.message).not.toContain("处理后继续");
    }
  });
});

describe("useDiscoveryState 判活与落点同源（SPEC 046 第五轮）", () => {
  // useDiscoveryState 在函数体内声明全部 ref（:91 起），每个实例各自独立：
  // 判活用到的事实（流程活动线、任务快照、暂停轮次）没有跨实例通道，
  // 每个用例直接造自己的现场即可，不需要任何"清场"装置。
  function makeFlowOnlyState(profileId: string): DiscoveryState {
    const state = useDiscoveryState({ profileId }, () => {});
    state.scrapeSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.screenSnapshot.value = { status: "failed", progress: {}, logs: [] };
    // 生产里这两份投影来自协调器的同一个 tick：本轮未结束（flowActive）与此刻有活体
    // （flowLiveWorker）。A 谓词不再在投影缺席时向 B 借道，所以现场必须把两份都摆齐。
    state.setFlowActive(true);
    state.setFlowLiveWorker(true);
    state.setFlowLocksNewRound(true);
    return state;
  }

  it("① 只因 Flow 投影而活：树干判到活时落点不得为空，且落点在可达清单里", () => {
    const state = makeFlowOnlyState("same-source-landing");
    expect(hasLiveTaskState(state)).toBe(true);
    const step = unfinishedRoundStep(state);
    expect(step).not.toBe("");
    expect(state.enabledSteps.value).toContain(step);
  });

  it("② 落点跟随流程投影的进行中阶段，取最深的一支（screen 优先于 search）", () => {
    const state = makeFlowOnlyState("same-source-projection");
    // 投影还没到达：流程活动线的最小开放面只有 02，落点不凭空造 03。
    expect(unfinishedRoundStep(state)).toBe("search");
    state.setFlowReachableSteps(new Set(["search", "screen"]));
    expect(state.enabledSteps.value).toEqual(["upload", "search", "screen"]);
    // 真实现场：并行流程抓取已完成、AI 筛选正在跑。02 页只渲染抓取列表，
    // 落 02 就看不到 03 正在跑的筛选进度，落点必须是最深的那一支。
    expect(unfinishedRoundStep(state)).toBe("screen");
    // 投影只开到 02（抓取仍在跑）时仍然落 02。
    state.setFlowReachableSteps(new Set(["search"]));
    expect(unfinishedRoundStep(state)).toBe("search");
  });

  it("③ 树干判到没有活任务 → 仍然不给落点，不凭空造一个", () => {
    const state = makeFlowOnlyState("same-source-idle");
    state.setFlowActive(false);
    state.setFlowLiveWorker(false);
    state.setFlowLocksNewRound(false);
    expect(hasLiveTaskState(state)).toBe(false);
    expect(unfinishedRoundStep(state)).toBe("");
  });

  it("④ 真实进度优先：流程活动线之上筛选确实在跑，落点仍是筛选进度页", () => {
    const state = makeFlowOnlyState("same-source-legacy");
    state.setFlowReachableSteps(new Set(["search", "screen"]));
    state.screenSnapshot.value = { status: "running", progress: {}, logs: [] };
    expect(unfinishedRoundStep(state)).toBe("screen");
    state.screenSnapshot.value = { status: "completed", progress: {}, logs: [] };
    state.scrapeSnapshot.value = { status: "queued", progress: {}, logs: [] };
    expect(unfinishedRoundStep(state)).toBe("search");
  });

  it("⑤ 正在看历史轮（enabledSteps 被收窄成 04）：落点仍跟随投影，不造不可达步骤", () => {
    const state = makeFlowOnlyState("same-source-history");
    state.setFlowReachableSteps(new Set(["search", "screen"]));
    state.historyRound.value = { runId: "h1", platform: "boss", status: "done", jobCount: 1 };
    // 「回到最新」在清掉历史轮之前求值落点：此刻 enabledSteps 只剩 04，
    // 拿它当判定面会把"有活任务就不去请求最新结果"的守卫判反。
    expect(state.enabledSteps.value).toEqual(["results"]);
    expect(hasLiveTaskState(state)).toBe(true);
    expect(unfinishedRoundStep(state)).toBe("screen");
  });

  it("⑥ 投影里没有任何进行中阶段 → 不给落点，结果页不当进度页", () => {
    const state = makeFlowOnlyState("same-source-results-only");
    state.setFlowReachableSteps(new Set(["results"]));
    expect(state.enabledSteps.value).toContain("results");
    expect(unfinishedRoundStep(state)).toBe("");
  });

  // SPEC 046 收尾守卫 + 收口第一单：判活（问题 A）、本轮未结束（问题 B）与「能不能开
  // 新一轮」是三份各自的事实。轨道排队中/运行中 → 页面投影成活体 → 判活为真（04 因此
  // 不接本轮结果，见 DiscoveryRecovery「仍有活体轨道时 04 不接本轮结果」）；已暂停 →
  // 没活体、本轮未结束、开新轮闸门锁住；已中断 → 没活体、本轮未结束，但开新轮闸门放行。
  it.each([
    { scene: "running", liveWorker: true, locksNewRound: true, newRoundBusy: true },
    { scene: "paused", liveWorker: false, locksNewRound: true, newRoundBusy: true },
    { scene: "interrupted", liveWorker: false, locksNewRound: false, newRoundBusy: false },
  ])("⑦ 活体 / 未结束 / 开新轮闸门三份投影分派（$scene）", (scene) => {
    const state = makeFlowOnlyState(`same-source-dispatch-${scene.scene}`);
    state.setFlowReachableSteps(new Set(["search", "screen"]));
    state.setFlowLiveWorker(scene.liveWorker);
    state.setFlowLocksNewRound(scene.locksNewRound);
    expect(hasLiveTaskState(state)).toBe(scene.liveWorker);
    // 三种现场都属于「这一轮还没结束」：本轮范围守卫不因判活或闸门放开而放松。
    expect(hasUnfinishedRound(state)).toBe(true);
    expect(state.scopeLocked.value).toBe(true);
    // 「能不能开新一轮」只由那一份清单回答：运行/暂停锁，中断放行。
    expect(state.pipelineBusy.value).toBe(scene.newRoundBusy);
    // 落点问的是未结束，不是活体：已中断的轮也必须有一个可达的真实进度页。
    expect(unfinishedRoundStep(state)).toBe("screen");
  });
});

// SPEC 046 V2 FR-011 / SC-005 / US4-1：页面可达性的唯一来源是 Flow 投影一处，
// 同一 Flow 的解锁集合只增不减。
// 缺陷现场：Flow 归属成立而投影还没到位（刷新重新水合、轮询间隙、投影给回更窄一份）时，
// 可达集合退回 ["upload","search"]，此前已解锁的 03/04 被重新锁住；同时可达集合还并了
// legacy 现场与活体任务探针，使「谁是权威」出现第二套口径。
describe("useDiscoveryState FR-011 可达性单一来源与投影空窗不回锁", () => {
  it("① 同一条 Flow 的投影回退或撤场都不回锁，撤场后才交回 legacy", () => {
    const state = useDiscoveryState({ profileId: "reachability-gap" }, () => {});
    state.analysisReady.value = true;
    state.setFlowReachableSteps(new Set(["search", "screen", "results"]), "flow-gap");
    expect(state.enabledSteps.value).toEqual(["upload", "search", "screen", "results"]);

    // 轮询间隙／重新水合基线：同一条 Flow 的投影暂时只给出 02，已解锁页不回锁。
    state.setFlowReachableSteps(new Set(["search"]), "flow-gap");
    expect(state.enabledSteps.value).toEqual(["upload", "search", "screen", "results"]);

    // 投影撤场（离开「全部」）：Flow 归属结束，可达性交回没有归属的 legacy 分支。
    state.setFlowReachableSteps(null);
    expect(state.enabledSteps.value).toEqual(["upload", "search"]);
  });

  it("② Flow 归属存在时可达集合只读投影一处，不再并 legacy 现场与活体探针", () => {
    const state = useDiscoveryState({ profileId: "reachability-single-source" }, () => {});
    // legacy 现场齐备（会给出 03/04）、AI 筛选快照仍在跑（探针会给出 03）。
    state.analysisReady.value = true;
    state.scrapeCompleted.value = true;
    state.resultLoaded.value = true;
    state.screenSnapshot.value = { status: "running", progress: {}, logs: [] };
    state.setFlowReachableSteps(new Set(["search"]));

    // 本轮 Flow 只开到 02：legacy 事实与探针都不许再加出一页。
    expect(state.enabledSteps.value).toEqual(["upload", "search"]);
  });

  it("③ 刷新恢复出的落点在投影只开到 02 时不得回锁", () => {
    const state = useDiscoveryState({ profileId: "reachability-restored-landing" }, () => {});
    const navigation = state as typeof state & {
      navigateStep: (step: string, options?: { source?: "restore" | "user" }) => string;
    };
    state.analysisReady.value = true;
    state.scrapeCompleted.value = true;
    state.resultLoaded.value = true;
    // 现场存档把用户放回 04（此刻还没有 Flow 归属，走 legacy 落点）。
    expect(navigation.navigateStep("results", { source: "restore" })).toBe("results");
    expect(state.enabledSteps.value).toEqual(["upload", "search", "screen", "results"]);

    // Flow 归属成立、投影刚起步只开到 02：刷新前的入口必须仍可进入。
    state.setFlowReachableSteps(new Set(["search"]), "flow-restored");
    expect(state.enabledSteps.value).toEqual(["upload", "search", "screen", "results"]);
  });

  it("④ 换轮、换画像与换到另一条 Flow 都清空水位，新一轮不继承上一轮入口", () => {
    const state = useDiscoveryState({ profileId: "reachability-new-round" }, () => {});
    state.analysisReady.value = true;
    state.setFlowReachableSteps(new Set(["search", "screen", "results"]), "flow-previous");
    expect(state.enabledSteps.value).toEqual(["upload", "search", "screen", "results"]);

    state.resetForProfileSwitch();
    expect(state.enabledSteps.value).toEqual(["upload"]);

    // 换画像后是另一条 Flow：即使不重置，投影带着新身份到达时也只给 01+02。
    state.setFlowReachableSteps(new Set(["search"]), "flow-next");
    expect(state.enabledSteps.value).toEqual(["upload", "search"]);

    // 同一页面上换到另一条 Flow（没有经过重置）同样不继承上一轮入口。
    const inherited = useDiscoveryState({ profileId: "reachability-new-flow" }, () => {});
    inherited.analysisReady.value = true;
    inherited.setFlowReachableSteps(new Set(["search", "screen", "results"]), "flow-old");
    inherited.setFlowReachableSteps(new Set(["search"]), "flow-new");
    expect(inherited.enabledSteps.value).toEqual(["upload", "search"]);
  });

  it("⑤ 没有 Flow 归属的旧形态仍由 legacy 现场与活体探针供页（树枝边界）", () => {
    const state = useDiscoveryState({ profileId: "reachability-legacy-only" }, () => {});
    state.analysisReady.value = true;
    state.scrapeCompleted.value = true;
    state.resultLoaded.value = true;
    expect(state.enabledSteps.value).toEqual(["upload", "search", "screen", "results"]);
    state.resultLoaded.value = false;
    state.screenSnapshot.value = { status: "running", progress: {}, logs: [] };
    expect(state.enabledSteps.value).toEqual(["upload", "search", "screen"]);
  });
});

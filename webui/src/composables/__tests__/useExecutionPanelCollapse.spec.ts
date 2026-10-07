import { computed, effectScope, nextTick, ref } from "vue";
import { stageSnapshotHasProgress, useExecutionPanelCollapse } from "../useExecutionPanelCollapse";

function setup(options: {
  scrapeProgress?: boolean;
  screenProgress?: boolean;
  historyMode?: boolean;
  sceneKey?: string;
} = {}) {
  const searchPanelsOpen = ref(true);
  const advancedPanelsOpen = ref(true);
  const screenPanelOpen = ref(true);
  const hasScrapeProgress = ref(Boolean(options.scrapeProgress));
  const hasScreenProgress = ref(Boolean(options.screenProgress));
  const historyMode = ref(Boolean(options.historyMode));
  const sceneKey = ref(options.sceneKey || "p1|e1");
  const scope = effectScope();
  scope.run(() => {
    useExecutionPanelCollapse({
      searchPanelsOpen,
      advancedPanelsOpen,
      screenPanelOpen,
      hasScrapeProgress,
      hasScreenProgress,
      historyMode,
      sceneKey,
    });
  });
  return {
    searchPanelsOpen, advancedPanelsOpen, screenPanelOpen,
    hasScrapeProgress, hasScreenProgress, historyMode, sceneKey, scope,
  };
}

afterEach(() => {
  // effect scopes in tests are stopped explicitly by each case via scope.stop().
});

describe("useExecutionPanelCollapse", () => {
  it("02: 抓取阶段首次出现真实进度时收拢 search+advanced，之后不重复抢手动展开", async () => {
    const ctx = setup();
    // 只有 loading（进度未出现）不能收拢配置。
    await nextTick();
    expect(ctx.searchPanelsOpen.value).toBe(true);
    expect(ctx.advancedPanelsOpen.value).toBe(true);

    ctx.hasScrapeProgress.value = true;
    await nextTick();
    expect(ctx.searchPanelsOpen.value).toBe(false);
    expect(ctx.advancedPanelsOpen.value).toBe(false);

    // 用户重新手动展开：同阶段后续进度（同一 bool 不变化时也不应关闭）。
    ctx.searchPanelsOpen.value = true;
    ctx.advancedPanelsOpen.value = true;
    await nextTick();
    await nextTick();
    expect(ctx.searchPanelsOpen.value).toBe(true);
    expect(ctx.advancedPanelsOpen.value).toBe(true);
    ctx.scope.stop();
  });

  it("03: 筛选阶段首次出现真实进度时收拢 screen 面板", async () => {
    const ctx = setup();
    ctx.hasScreenProgress.value = true;
    await nextTick();
    expect(ctx.screenPanelOpen.value).toBe(false);
    // 02 的两个面板不受 03 进度影响。
    expect(ctx.searchPanelsOpen.value).toBe(true);
    expect(ctx.advancedPanelsOpen.value).toBe(true);
    ctx.scope.stop();
  });

  it("自动 AI 交接：兄弟轨道随后到达时各自阶段只收拢一次", async () => {
    const ctx = setup();
    ctx.hasScrapeProgress.value = true;
    await nextTick();
    expect(ctx.searchPanelsOpen.value).toBe(false);
    // 兄弟轨道在 03 才到达筛选进度：screen 也收拢。
    ctx.hasScreenProgress.value = true;
    await nextTick();
    expect(ctx.screenPanelOpen.value).toBe(false);
    ctx.scope.stop();
  });

  it("历史只读现场不能冒充执行启动", async () => {
    const ctx = setup({ scrapeProgress: true, screenProgress: true, historyMode: true });
    await nextTick();
    expect(ctx.searchPanelsOpen.value).toBe(true);
    expect(ctx.advancedPanelsOpen.value).toBe(true);
    expect(ctx.screenPanelOpen.value).toBe(true);
    ctx.scope.stop();
  });

  it("scene 身份切换后，新阶段首次进度重新触发一次", async () => {
    const ctx = setup();
    ctx.hasScrapeProgress.value = true;
    await nextTick();
    expect(ctx.searchPanelsOpen.value).toBe(false);

    // 新一轮 scene：进度尚未出现时用户手动展开（模拟无存档现场），
    // 新阶段首次进度到达后再次收拢一次。
    ctx.hasScrapeProgress.value = false;
    ctx.sceneKey.value = "p1|e2";
    await nextTick();
    ctx.searchPanelsOpen.value = true;
    ctx.advancedPanelsOpen.value = true;
    await nextTick();
    ctx.hasScrapeProgress.value = true;
    await nextTick();
    expect(ctx.searchPanelsOpen.value).toBe(false);
    expect(ctx.advancedPanelsOpen.value).toBe(false);
    ctx.scope.stop();
  });

  it("进度一直为 false 时永远不动配置（请求被拒绝 / 尚未开始）", async () => {
    const ctx = setup();
    for (let index = 0; index < 3; index += 1) {
      await nextTick();
    }
    expect(ctx.searchPanelsOpen.value).toBe(true);
    expect(ctx.advancedPanelsOpen.value).toBe(true);
    expect(ctx.screenPanelOpen.value).toBe(true);
    ctx.scope.stop();
  });

  it("computed 进度源同样触发（协调器投影用法）", async () => {
    const progress = ref(false);
    const searchPanelsOpen = ref(true);
    const advancedPanelsOpen = ref(true);
    const screenPanelOpen = ref(true);
    const scope = effectScope();
    scope.run(() => {
      useExecutionPanelCollapse({
        searchPanelsOpen,
        advancedPanelsOpen,
        screenPanelOpen,
        hasScrapeProgress: computed(() => progress.value),
        hasScreenProgress: computed(() => false),
        historyMode: computed(() => false),
        sceneKey: computed(() => "p1|computed"),
      });
    });
    progress.value = true;
    await nextTick();
    expect(searchPanelsOpen.value).toBe(false);
    expect(advancedPanelsOpen.value).toBe(false);
    scope.stop();
  });
});

describe("stageSnapshotHasProgress", () => {
  it("只有文案的启动占位/失败提示不算真实进度", () => {
    expect(stageSnapshotHasProgress(null)).toBe(false);
    expect(stageSnapshotHasProgress(undefined)).toBe(false);
    expect(stageSnapshotHasProgress({ progress: {} })).toBe(false);
    expect(stageSnapshotHasProgress({ progress: { message: "正在创建抓取任务…" } })).toBe(false);
    expect(stageSnapshotHasProgress({ progress: { message: "抓取启动失败" } })).toBe(false);
    expect(stageSnapshotHasProgress({ progress: { overall_percent: 0, current: 0, total: 0 } })).toBe(false);
  });

  it("任一真实推进数字出现即算进度（与卡体进度锚点同字段）", () => {
    expect(stageSnapshotHasProgress({ progress: { overall_percent: 1 } })).toBe(true);
    expect(stageSnapshotHasProgress({ progress: { current: 1 } })).toBe(true);
    expect(stageSnapshotHasProgress({ progress: { total: 4 } })).toBe(true);
    // 旧接口把 progress 直接写成数字百分比的形状仍兼容。
    expect(stageSnapshotHasProgress({ progress: 0 })).toBe(false);
    expect(stageSnapshotHasProgress({ progress: 12 })).toBe(true);
  });
});

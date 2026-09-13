import { describe, expect, it, vi } from "vitest";
import { computed, ref } from "vue";
import { useResumeAnalysisFlow } from "../useResumeAnalysisFlow";

describe("useResumeAnalysisFlow", () => {
  const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

  it("启动分析后不锁住页面，成功时落到搜索页", async () => {
    let resolveRequest: (value: { fields: { keywords: string[] } }) => void = () => undefined;
    const request = new Promise<{ fields: { keywords: string[] } }>((resolve) => {
      resolveRequest = resolve;
    });
    const activeStep = ref("upload");
    const uploadBusy = ref(false);
    const resumeError = ref("");
    const resumeAnalysis = ref<unknown>(null);
    const entered: string[] = [];
    const flow = useResumeAnalysisFlow({
      refs: { activeStep, uploadBusy, resumeError, resumeAnalysis },
      api: {
        postAnalyzeResume: () => request,
        cancelActiveTasksForNewRound: async () => true,
        clearLatestResult: async () => true,
        enterSearchStep: () => {
          activeStep.value = "search";
          entered.push("search");
        },
        notify: () => undefined,
      },
    });

    flow.startAnalysis({ file: new File(["resume"], "resume.pdf"), platform: "boss", aiConsent: true });
    expect(flow.phase.value).toBe("analyzing");
    expect(uploadBusy.value).toBe(true);

    resolveRequest({ fields: { keywords: ["Vue"] } });
    await flush();

    expect(flow.phase.value).toBe("succeeded");
    expect(resumeAnalysis.value).toEqual({ fields: { keywords: ["Vue"] } });
    expect(entered).toEqual(["search"]);
    expect(uploadBusy.value).toBe(false);
  });

  it("开始新的分析时先清掉上一份分析结果", () => {
    const activeStep = ref("upload");
    const uploadBusy = ref(false);
    const resumeError = ref("");
    const resumeAnalysis = ref<unknown>({ fields: { keywords: ["旧关键词"] } });
    const flow = useResumeAnalysisFlow({
      refs: { activeStep, uploadBusy, resumeError, resumeAnalysis },
      api: {
        postAnalyzeResume: () => new Promise(() => undefined),
        cancelActiveTasksForNewRound: async () => true,
        clearLatestResult: async () => true,
        enterSearchStep: () => undefined,
        notify: () => undefined,
      },
    });

    flow.startAnalysis({ file: new File(["new resume"], "new.pdf"), platform: "boss", aiConsent: true });

    expect(resumeAnalysis.value).toBeNull();
  });

  it("分析失败时保留真实错误，不伪装成浏览器或刷新失败", async () => {
    const activeStep = ref("upload");
    const uploadBusy = ref(false);
    const resumeError = ref("");
    const resumeAnalysis = ref<unknown>(null);
    const error = new Error("服务端暂时不可用");
    const flow = useResumeAnalysisFlow({
      refs: { activeStep, uploadBusy, resumeError, resumeAnalysis },
      api: {
        postAnalyzeResume: async () => {
          throw error;
        },
        cancelActiveTasksForNewRound: async () => true,
        clearLatestResult: async () => true,
        enterSearchStep: () => undefined,
        notify: () => undefined,
      },
    });

    flow.startAnalysis({ file: new File(["resume"], "resume.pdf"), platform: "boss", aiConsent: true });
    await flush();

    expect(flow.phase.value).toBe("failed");
    expect(resumeError.value).toContain("服务端暂时不可用");
    expect(resumeError.value).not.toContain("浏览器");
    expect(resumeError.value).not.toContain("刷新");
    expect(uploadBusy.value).toBe(false);
  });

  it("分析尚未结束时返回上传页，结束后再进入搜索页", async () => {
    let resolveRequest: (value: Record<string, unknown>) => void = () => undefined;
    const request = new Promise<Record<string, unknown>>((resolve) => {
      resolveRequest = resolve;
    });
    const activeStep = ref("upload");
    const uploadBusy = ref(false);
    const resumeError = ref("");
    const resumeAnalysis = ref<unknown>(null);
    const flow = useResumeAnalysisFlow({
      refs: { activeStep, uploadBusy, resumeError, resumeAnalysis },
      api: {
        postAnalyzeResume: () => request,
        cancelActiveTasksForNewRound: async () => true,
        clearLatestResult: async () => true,
        enterSearchStep: () => {
          activeStep.value = "search";
        },
        notify: () => undefined,
      },
    });

    flow.startAnalysis({ file: new File(["resume"], "resume.pdf"), platform: "boss", aiConsent: true });
    flow.landOnReturn();
    expect(activeStep.value).toBe("upload");
    expect(computed(() => flow.phase.value).value).toBe("analyzing");

    resolveRequest({});
    await flush();
    expect(activeStep.value).toBe("search");
  });

  it("没有简历分析时回到最新不改变当前历史结果页", () => {
    const activeStep = ref("results");
    const uploadBusy = ref(false);
    const resumeError = ref("");
    const resumeAnalysis = ref<unknown>(null);
    const flow = useResumeAnalysisFlow({
      refs: { activeStep, uploadBusy, resumeError, resumeAnalysis },
      api: {
        postAnalyzeResume: async () => ({}),
        cancelActiveTasksForNewRound: async () => true,
        clearLatestResult: async () => true,
        enterSearchStep: () => { activeStep.value = "search"; },
        notify: () => undefined,
      },
    });

    flow.landOnReturn();

    expect(activeStep.value).toBe("results");
  });

  it("旧任务清理被拒绝时显示真实阻断原因", async () => {
    const activeStep = ref("upload");
    const uploadBusy = ref(false);
    const resumeError = ref("");
    const resumeAnalysis = ref<unknown>(null);
    const notices: string[] = [];
    const flow = useResumeAnalysisFlow({
      refs: { activeStep, uploadBusy, resumeError, resumeAnalysis },
      api: {
        postAnalyzeResume: async () => ({}),
        cancelActiveTasksForNewRound: async () => false,
        clearLatestResult: async () => true,
        enterSearchStep: () => undefined,
        notify: (message) => notices.push(message),
      },
    });

    flow.startAnalysis({ file: new File(["resume"], "resume.pdf"), platform: "boss", aiConsent: true });
    await flush();

    expect(flow.phase.value).toBe("failed");
    expect(resumeError.value).toBe("旧任务未能安全结束，简历分析未开始");
    expect(notices).toContain(resumeError.value);
    expect(uploadBusy.value).toBe(false);
  });

  it("后台任务：运行中保持分析态，完成后应用结果并进入搜索页", async () => {
    const activeStep = ref("upload");
    const uploadBusy = ref(false);
    const resumeError = ref("");
    const resumeAnalysis = ref<unknown>(null);
    const entered: string[] = [];
    const applied: unknown[] = [];
    const states: { status: string; result?: unknown }[] = [
      { status: "running" },
      { status: "done", result: { fields: { profile_summary: "画像" } } },
    ];
    let call = 0;
    const flow = useResumeAnalysisFlow({
      refs: { activeStep, uploadBusy, resumeError, resumeAnalysis },
      api: {
        postAnalyzeResume: async () => ({ ok: true, task_id: "resume-analysis-1" }),
        fetchTaskState: async () => states[Math.min(call++, states.length - 1)],
        cancelActiveTasksForNewRound: async () => true,
        clearLatestResult: async () => true,
        enterSearchStep: () => {
          activeStep.value = "search";
          entered.push("search");
        },
        notify: () => undefined,
      },
      onAnalysisSuccess: (data) => applied.push(data),
      pollIntervalMs: 1,
    });

    flow.startAnalysis({ file: new File(["resume"], "resume.pdf"), platform: "boss", aiConsent: true });
    // 条件等待代替固定时长：轮询链路（含 1ms 轮询间隔）在负载高时
    // 可能超过固定等待，导致假失败；断言本身不放松。
    await vi.waitFor(
      () => { expect(flow.phase.value).toBe("succeeded"); },
      { timeout: 2000, interval: 5 },
    );

    expect(call).toBeGreaterThanOrEqual(2);
    expect(resumeAnalysis.value).toEqual({ fields: { profile_summary: "画像" } });
    expect(applied).toEqual([{ fields: { profile_summary: "画像" } }]);
    expect(entered).toEqual(["search"]);
    expect(uploadBusy.value).toBe(false);
  });

  it("后台任务：完成结果只应用一次，重复轮询不重复投影", async () => {
    const activeStep = ref("upload");
    const uploadBusy = ref(false);
    const resumeError = ref("");
    const resumeAnalysis = ref<unknown>(null);
    const applied: unknown[] = [];
    const flow = useResumeAnalysisFlow({
      refs: { activeStep, uploadBusy, resumeError, resumeAnalysis },
      api: {
        postAnalyzeResume: async () => ({ ok: true, task_id: "resume-analysis-2" }),
        fetchTaskState: async () => ({ status: "done", result: { fields: { profile_summary: "画像" } } }),
        cancelActiveTasksForNewRound: async () => true,
        clearLatestResult: async () => true,
        enterSearchStep: () => undefined,
        notify: () => undefined,
      },
      onAnalysisSuccess: (data) => applied.push(data),
      pollIntervalMs: 1,
    });

    flow.startAnalysis({ file: new File(["resume"], "resume.pdf"), platform: "boss", aiConsent: true });
    await vi.waitFor(
      () => { expect(applied).toHaveLength(1); },
      { timeout: 2000, interval: 5 },
    );
  });

  it("刷新接回：任务运行中保持分析中，完成后自动接回结果", async () => {
    const activeStep = ref("upload");
    const uploadBusy = ref(false);
    const resumeError = ref("");
    const resumeAnalysis = ref<unknown>(null);
    const states: { status: string; result?: unknown }[] = [
      { status: "running" },
      { status: "running" },
      { status: "done", result: { fields: { profile_summary: "画像" } } },
    ];
    let call = 0;
    const flow = useResumeAnalysisFlow({
      refs: { activeStep, uploadBusy, resumeError, resumeAnalysis },
      api: {
        postAnalyzeResume: async () => ({}),
        fetchTaskState: async () => states[Math.min(call++, states.length - 1)],
        cancelActiveTasksForNewRound: async () => true,
        clearLatestResult: async () => true,
        enterSearchStep: () => { activeStep.value = "search"; },
        notify: () => undefined,
      },
      pollIntervalMs: 1,
    });

    flow.restore("analyzing", "", "resume-analysis-9");
    expect(flow.phase.value).toBe("analyzing");
    expect(uploadBusy.value).toBe(true);

    // 条件等待代替固定时长（该链路含 3 次轮询拍），断言本身不放松。
    await vi.waitFor(
      () => { expect(flow.phase.value).toBe("succeeded"); },
      { timeout: 2000, interval: 5 },
    );

    expect(resumeAnalysis.value).toEqual({ fields: { profile_summary: "画像" } });
    expect(activeStep.value).toBe("search");
    expect(uploadBusy.value).toBe(false);
  });

  it("刷新接回：任务已失败时展示真实失败原因，不伪装成刷新失败", async () => {
    const activeStep = ref("upload");
    const uploadBusy = ref(false);
    const resumeError = ref("");
    const resumeAnalysis = ref<unknown>(null);
    const flow = useResumeAnalysisFlow({
      refs: { activeStep, uploadBusy, resumeError, resumeAnalysis },
      api: {
        postAnalyzeResume: async () => ({}),
        fetchTaskState: async () => ({ status: "failed", error: "服务端暂时不可用" }),
        cancelActiveTasksForNewRound: async () => true,
        clearLatestResult: async () => true,
        enterSearchStep: () => { activeStep.value = "search"; },
        notify: () => undefined,
      },
      pollIntervalMs: 1,
    });

    flow.restore("analyzing", "", "resume-analysis-10");
    await vi.waitFor(
      () => { expect(flow.phase.value).toBe("failed"); },
      { timeout: 2000, interval: 5 },
    );

    expect(resumeError.value).toContain("服务端暂时不可用");
    expect(resumeError.value).not.toContain("浏览器");
    expect(activeStep.value).toBe("upload");
  });
});

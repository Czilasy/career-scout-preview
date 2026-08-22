import {
  backfillJobPlatform,
  buildSearchScriptParams,
  classifyTaskSize,
  createAsyncResourceLoader,
  createPlatformState,
  DEFAULT_PLATFORM,
  filterPipelineResultByPlatform,
  normalizeScopePreview,
  partitionPipelineResult,
  projectResumeSuggestionToSchema,
  recoverSelectionSettings,
  historyStatusLabel,
  roundScopeLabel,
  shouldConfirmNationalScope,
  type PipelineResult,
} from "../discovery";
import type {
  AdvancedSettingsState,
  JobItem,
  Platform,
  PlatformFilterSchema,
  ScopePreviewResponse,
} from "../types";

describe("discovery helpers", () => {
  it("keeps uncertain AI results separate from real matches", () => {
    const groups = partitionPipelineResult({
      jobs: [
        { job_id: "m", title: "匹配", verdict: "match" },
        { job_id: "n", title: "不匹配", verdict: "not_match" },
        { job_id: "u", title: "待确认", verdict: "uncertain" },
      ],
      dropped: [{ job_id: "d", title: "粗筛剔除" }],
    });

    expect(groups.matched.map((job) => job.job_id)).toEqual(["m"]);
    expect(groups.unmatched.map((job) => job.job_id)).toEqual(["n"]);
    expect(groups.uncertain.map((job) => job.job_id)).toEqual(["u"]);
    expect(groups.dropped.map((job) => job.job_id)).toEqual(["d"]);
  });

  it("builds broad-search parameters from only keywords and cities", () => {
    expect(buildSearchScriptParams(
      ["Python 后端", " Python 后端 ", "FastAPI"],
      ["上海", " 杭州 ", ""],
    )).toEqual({
      keyword: "Python 后端,FastAPI",
      city: ["上海", "杭州"],
      filters: {},
    });
  });

  it("B042: confirms national scope only when keyword exists and city is empty", () => {
    expect(shouldConfirmNationalScope(["Python"], [])).toBe(true);
    expect(shouldConfirmNationalScope(["Python"], ["  "])).toBe(true);
    expect(shouldConfirmNationalScope([], [])).toBe(false);
    expect(shouldConfirmNationalScope([], ["上海"])).toBe(false);
    expect(shouldConfirmNationalScope(["Python"], ["上海"])).toBe(false);
  });

  it.each([
    [1, "small"],
    [9, "small"],
    [10, "medium"],
    [49, "medium"],
    [50, "large"],
    [200, "large"],
  ] as const)("classifies %i planned pages as %s", (pages, expected) => {
    expect(classifyTaskSize(pages)).toBe(expected);
  });

  it.each([0, 201])("rejects %i planned pages outside the valid range", (pages) => {
    expect(() => classifyTaskSize(pages)).toThrow(RangeError);
  });

  it("normalizes the backend-authoritative scope preview", () => {
    const response: ScopePreviewResponse = {
      ok: true,
      scope: {
        keywords: ["AI应用开发"],
        scope_kind: "cities",
        cities: ["东莞"],
        pages_per_combination: 3,
        combination_count: 1,
        planned_pages: 3,
        task_size: "small",
        scope_digest: "sha256:scope",
      },
      deduplicated: { keywords: ["ai应用开发"], cities: ["东莞市"] },
    };

    expect(normalizeScopePreview(response)).toEqual(response.scope);
  });

  it("projects resume semantic labels into the target platform schema", () => {
    const zhilianSchema: PlatformFilterSchema = {
      ok: true, platform: "zhilian", schema_version: 2, enabled_for_new_tasks: true,
      fields: [
        { key: "experience", label: "经验要求", multiple: true, options: [{ value: "0305", label: "3-5年" }] },
        { key: "company_nature", label: "公司性质", multiple: true, options: [{ value: "1", label: "国企" }] },
      ],
    };
    const projected = projectResumeSuggestionToSchema(
      { experience: ["3-5年"], company_nature: ["国企"], stage: ["B轮"] },
      zhilianSchema,
    );
    expect(projected).toEqual({
      experience: ["0305"],
      company_nature: ["1"],
    });
    expect(projected.stage).toBeUndefined();
  });

  it("restores all execution custom fields including pages", () => {
    const settings = {
      pages: 3,
      inter_combo_delay: 10,
      detail_batch_size: 15,
      detail_interval: 2,
      detail_reset_every: 4,
      detail_batch_cooldown: 5,
      detail_tab_pool_size: 5,
      screen_batch_size: 50,
      screen_concurrency: 5,
      match_batch_size: 4,
      match_concurrency: 10,
    };
    const state: AdvancedSettingsState = {
      ok: true,
      selection: "stable",
      settings,
      last_custom: { config_digest: "sha256:custom", settings: { ...settings, detail_batch_size: 8 } },
      mode_version: {
        id: "mode-v1",
        version_digest: "sha256:mode",
        available_modes: ["stable", "balanced", "extreme"],
      },
      manual_ranges: {},
      config_schema_version: 1,
    };

    expect(recoverSelectionSettings(state, "custom")).toEqual({
      ...settings,
      detail_batch_size: 8,
    });
    expect(recoverSelectionSettings(state, "stable")).toEqual(settings);
    // R4：pages 属于自定义保存字段，恢复时必须保留
    expect(recoverSelectionSettings(state, "custom")).toHaveProperty("pages", 3);
  });

  // ------------------------------------------------------------------
  // 结果页平台筛选（回归：切换筛选后数据归 0）
  // ------------------------------------------------------------------
  function mixedResult(): PipelineResult {
    return {
      ok: true,
      jobs: [
        { job_id: "b1", platform: "boss", title: "BOSS 岗位", verdict: "match" },
        { job_id: "z1", platform: "zhilian", title: "智联岗位", verdict: "match" },
        { job_id: "b2", platform: "boss", title: "BOSS 待确认", verdict: "uncertain" },
        { job_id: "z2", platform: "zhilian", title: "智联不匹配", verdict: "not_match" },
      ] as JobItem[],
      dropped: [
        { job_id: "b3", platform: "boss", title: "BOSS 剔除" },
        { job_id: "z3", platform: "zhilian", title: "智联剔除" },
      ] as JobItem[],
    };
  }

  it("keeps all jobs when the filter is 'all'", () => {
    const result = filterPipelineResultByPlatform(mixedResult(), "all");
    expect(result.jobs).toHaveLength(4);
    expect(result.dropped).toHaveLength(2);
    // “全部”视图不产生新对象，避免无谓的响应式重算。
    expect(filterPipelineResultByPlatform(mixedResult(), "all")).toEqual(mixedResult());
  });

  it("filters jobs and dropped by platform without losing the other platform", () => {
    const boss = filterPipelineResultByPlatform(mixedResult(), "boss");
    expect(boss.jobs?.map((job) => job.job_id)).toEqual(["b1", "b2"]);
    expect(boss.dropped?.map((job) => job.job_id)).toEqual(["b3"]);

    const zhilian = filterPipelineResultByPlatform(mixedResult(), "zhilian");
    expect(zhilian.jobs?.map((job) => job.job_id)).toEqual(["z1", "z2"]);
    expect(zhilian.dropped?.map((job) => job.job_id)).toEqual(["z3"]);
  });

  it("preserves group counts after switching filters back and forth", () => {
    const full = mixedResult();
    // 模拟用户操作：全部 → boss → 全部 → zhilian → 全部
    for (const filter of ["boss", "all", "zhilian", "all"] as const) {
      const filtered = filterPipelineResultByPlatform(full, filter);
      const groups = partitionPipelineResult(filtered);
      const total = groups.matched.length + groups.unmatched.length
        + groups.uncertain.length + groups.dropped.length;
      // 切换筛选不得丢数据：总数只随平台视图收缩/恢复，回“全部”必须复原。
      if (filter === "all") {
        expect(total).toBe(6);
      }
    }
  });

  it("drops jobs whose platform is missing when a single platform is selected", () => {
    // 缺 platform 的岗位（旧后端内存结果）在单平台视图下被过滤掉——
    // 这正是“归 0”根因；回填函数负责在进入视图前补上平台身份。
    const result = filterPipelineResultByPlatform(
      { jobs: [{ job_id: "x1", title: "无平台" }] as JobItem[] },
      "boss",
    );
    expect(result.jobs).toHaveLength(0);
  });

  it("backfills missing job platform from the authoritative task platform", () => {
    const result = backfillJobPlatform(
      {
        jobs: [
          { job_id: "x1", title: "无平台" },
          { job_id: "x2", platform: "zhilian", title: "有平台" },
        ] as JobItem[],
        dropped: [{ job_id: "x3", title: "剔除无平台" }] as JobItem[],
      },
      "boss",
    );
    expect(result.jobs?.[0]).toMatchObject({ platform: "boss" });
    expect(result.jobs?.[1]).toMatchObject({ platform: "zhilian" }); // 不回填已有平台
    expect(result.dropped?.[0]).toMatchObject({ platform: "boss" });
  });

  it("backfill with no platform leaves jobs untouched", () => {
    const result = backfillJobPlatform(
      { jobs: [{ job_id: "x1", title: "无平台" }] as JobItem[] },
      undefined,
    );
    expect(result.jobs?.[0]).not.toHaveProperty("platform");
  });
});

describe("platform state (T502)", () => {
  // 不变式锚点：contracts/platform-schema.md L142-159 + tasks006.md L27
  // 三身份独立：草稿平台 / 任务平台 / 最近结果平台，不能复用一个变量互相改写。

  it("defaults draft to boss and leaves task/result unbound", () => {
    const state = createPlatformState();
    expect(state.draft).toBe("boss");
    expect(state.task).toBeNull();
    expect(state.result).toBeNull();
  });

  it("honors an explicit initial draft platform", () => {
    const state = createPlatformState("zhilian");
    expect(state.draft).toBe("zhilian");
    expect(state.task).toBeNull();
    expect(state.result).toBeNull();
  });

  it("setDraftPlatform changes only the draft, not task or result", () => {
    const state = createPlatformState();
    state.setTaskPlatform("zhilian");
    state.setResultPlatform("zhilian");

    state.setDraftPlatform("zhilian");

    expect(state.draft).toBe("zhilian");
    expect(state.task).toBe("zhilian"); // unchanged
    expect(state.result).toBe("zhilian"); // unchanged
  });

  it("setTaskPlatform changes only the task, not draft or result", () => {
    const state = createPlatformState();
    state.setResultPlatform("zhilian");

    state.setTaskPlatform("boss");

    expect(state.task).toBe("boss");
    expect(state.draft).toBe("boss"); // unchanged (coincidentally same default)
    expect(state.result).toBe("zhilian"); // unchanged
  });

  it("setTaskPlatform(null) clears the task without touching draft or result", () => {
    const state = createPlatformState("zhilian");
    state.setTaskPlatform("boss");
    state.setResultPlatform("boss");

    state.setTaskPlatform(null);

    expect(state.task).toBeNull();
    expect(state.draft).toBe("zhilian"); // unchanged
    expect(state.result).toBe("boss"); // unchanged
  });

  it("setResultPlatform changes only the result, not draft or task", () => {
    const state = createPlatformState();
    state.setTaskPlatform("zhilian");

    state.setResultPlatform("zhilian");

    expect(state.result).toBe("zhilian");
    expect(state.draft).toBe("boss"); // unchanged, no draft switch trigger
    expect(state.task).toBe("zhilian"); // unchanged
  });

  it("setResultPlatform(null) clears the result without triggering a draft switch", () => {
    const state = createPlatformState();
    state.setResultPlatform("zhilian");
    state.setDraftPlatform("zhilian");

    state.setResultPlatform(null);

    expect(state.result).toBeNull();
    expect(state.draft).toBe("zhilian"); // unchanged
    expect(state.task).toBeNull(); // unchanged
  });

  it("keeps three identities independent across interleaved operations", () => {
    const state = createPlatformState();

    state.setDraftPlatform("zhilian");
    state.setTaskPlatform("boss");
    state.setResultPlatform("zhilian");
    state.setDraftPlatform("boss");
    state.setResultPlatform("boss");

    // 三者此刻碰巧都是 boss，但各自独立；任何 setter 改其一不应波及其它两个。
    expect(state.draft).toBe("boss");
    expect(state.task).toBe("boss");
    expect(state.result).toBe("boss");

    state.setDraftPlatform("zhilian");
    expect(state.task).toBe("boss");
    expect(state.result).toBe("boss");

    state.setTaskPlatform("zhilian");
    expect(state.draft).toBe("zhilian");
    expect(state.result).toBe("boss");
  });

  it("creates independent instances per call (no shared state)", () => {
    const a = createPlatformState();
    const b = createPlatformState();

    a.setDraftPlatform("zhilian");
    a.setTaskPlatform("boss");
    a.setResultPlatform("zhilian");

    expect(b.draft).toBe(DEFAULT_PLATFORM);
    expect(b.task).toBeNull();
    expect(b.result).toBeNull();
  });

  it("type-checks platform values at compile time", () => {
    // 编译时断言：setter 不接受 Platform 之外的字符串。
    const state = createPlatformState();
    const draft: Platform = state.draft;
    const task: Platform | null = state.task;
    const result: Platform | null = state.result;
    state.setDraftPlatform(draft);
    state.setTaskPlatform(task);
    state.setResultPlatform(result);
    expect(true).toBe(true);
  });
});

describe("platform schema race (T504)", () => {
  // 不变式锚点：contracts/platform-schema.md L151-156 + tasks006.md L35-37
  // 首次应用异步响应前，必须有请求序号或取消机制测试，证明旧平台响应晚到不会覆盖当前平台。
  // 首次应用简历建议前，必须按当前已加载 schema 投影。

  function makeSchema(platform: Platform): PlatformFilterSchema {
    return {
      ok: true,
      platform,
      schema_version: 1,
      enabled_for_new_tasks: true,
      fields: platform === "boss"
        ? [{ key: "stage", label: "融资阶段", multiple: false, options: [] }]
        : [{ key: "company_nature", label: "公司性质", multiple: false, options: [] }],
    };
  }

  it("100 交错切换乱序 resolve：只有最后一次被采纳", async () => {
    const loader = createAsyncResourceLoader<PlatformFilterSchema>();
    // 受控 fetcher：每次调用注册一个 { resolve, reject }，由测试决定何时回应。
    const pending: Array<{
      platform: Platform;
      resolve: (value: PlatformFilterSchema) => void;
      reject: (reason: unknown) => void;
      signal: AbortSignal;
    }> = [];
    const fetcher = (platform: Platform, signal: AbortSignal) =>
      new Promise<PlatformFilterSchema>((resolve, reject) => {
        pending.push({ platform, resolve, reject, signal });
      });

    // 发起 100 次切换：boss/zhilian 交替
    const platforms: Platform[] = Array.from({ length: 100 }, (_, i) =>
      i % 2 === 0 ? "boss" : "zhilian",
    );
    const promises = platforms.map((p) => loader.load(p, fetcher));
    expect(pending.length).toBe(100);

    // 乱序 resolve：用倒序（旧请求最后 resolve，模拟"旧响应晚到"）
    for (let i = pending.length - 1; i >= 0; i--) {
      pending[i].resolve(makeSchema(pending[i].platform));
    }
    const results = await Promise.all(promises);

    // 只有最后一次（i=99）的响应应被采纳
    expect(results.filter((accepted) => accepted === true)).toHaveLength(1);
    const lastPlatform = platforms[99];
    expect(loader.loadedPlatform).toBe(lastPlatform);
    expect(loader.data?.platform).toBe(lastPlatform);
    expect(loader.pendingPlatform).toBeNull();
    expect(loader.error).toBeNull();
    // 字段与最终平台匹配（boss→stage，zhilian→company_nature）
    if (lastPlatform === "boss") {
      expect(loader.data?.fields[0]?.key).toBe("stage");
    } else {
      expect(loader.data?.fields[0]?.key).toBe("company_nature");
    }
  });

  it("旧请求 reject 不污染当前 error 状态（错误归属）", async () => {
    const loader = createAsyncResourceLoader<PlatformFilterSchema>();
    const pending: Array<{
      resolve: (value: PlatformFilterSchema) => void;
      reject: (reason: unknown) => void;
    }> = [];
    const fetcher = () =>
      new Promise<PlatformFilterSchema>((resolve, reject) => {
        pending.push({ resolve, reject });
      });

    // 第一次请求 boss，pending 中
    const p1 = loader.load("boss", fetcher);
    expect(loader.pendingPlatform).toBe("boss");

    // 第二次请求 zhilian，应取消第一次
    const p2 = loader.load("zhilian", fetcher);
    expect(loader.pendingPlatform).toBe("zhilian");

    // 第一次 reject（旧请求失败）
    pending[0].reject(new Error("boss 网络抖动"));
    // 第二次 resolve（成功）
    pending[1].resolve(makeSchema("zhilian"));

    const [r1, r2] = await Promise.all([p1, p2]);
    expect(r1).toBe(false); // 旧请求被丢弃
    expect(r2).toBe(true); // 新请求被采纳
    expect(loader.error).toBeNull(); // 旧请求的错误没污染
    expect(loader.loadedPlatform).toBe("zhilian");
  });

  it("最新请求失败时 error 被正确记录", async () => {
    const loader = createAsyncResourceLoader<PlatformFilterSchema>();
    const fetcherOk = () => Promise.resolve(makeSchema("boss"));
    const fetcherErr = () => Promise.reject(new Error("zhilian 503"));

    // 第一次 boss 成功
    const r1 = await loader.load("boss", fetcherOk);
    expect(r1).toBe(true);
    expect(loader.loadedPlatform).toBe("boss");
    expect(loader.error).toBeNull();

    // 第二次 zhilian 失败
    const r2 = await loader.load("zhilian", fetcherErr);
    expect(r2).toBe(false);
    expect(loader.loadedPlatform).toBe("boss"); // 保留上次成功
    expect(loader.error).toBe("zhilian 503");
  });

  it("响应 platform 与请求 platform 不匹配被丢弃", async () => {
    const loader = createAsyncResourceLoader<PlatformFilterSchema>();
    // 请求 boss 但响应说 zhilian（后端串台）
    const fetcher = () => Promise.resolve(makeSchema("zhilian"));
    const r = await loader.load("boss", fetcher);
    expect(r).toBe(false);
    expect(loader.loadedPlatform).toBeNull();
    expect(loader.data).toBeNull();
    expect(loader.error).toBeNull(); // 平台不匹配不算错误，只算丢弃
  });

  it("cancel() 后在途请求的响应被丢弃，后续 load 仍正常", async () => {
    const loader = createAsyncResourceLoader<PlatformFilterSchema>();
    const pending: Array<{
      resolve: (value: PlatformFilterSchema) => void;
    }> = [];
    const fetcher = () =>
      new Promise<PlatformFilterSchema>((resolve) => {
        pending.push({ resolve });
      });

    const p1 = loader.load("boss", fetcher);
    expect(loader.pendingPlatform).toBe("boss");

    loader.cancel();
    expect(loader.pendingPlatform).toBeNull();

    // 取消后再 resolve 旧请求：应被丢弃
    pending[0].resolve(makeSchema("boss"));
    expect(await p1).toBe(false);
    expect(loader.loadedPlatform).toBeNull();

    // 后续 load 仍正常工作
    const r2 = await loader.load("zhilian", () => Promise.resolve(makeSchema("zhilian")));
    expect(r2).toBe(true);
    expect(loader.loadedPlatform).toBe("zhilian");
  });
});

describe("historyStatusLabel (B038)", () => {
  it("maps scraped_only to 已抓取，未筛选 and keeps three allowed labels", () => {
    expect(historyStatusLabel("scraped_only", 3)).toBe("已抓取，未筛选");
    expect(historyStatusLabel("done", 3)).toBe("完成");
    expect(historyStatusLabel("partial", 3)).toBe("部分结果");
    // 017-US3: 标签只有三种；未知状态不渲染（失败/取消轮已不再产生）
    expect(historyStatusLabel("failed", 3)).toBe("");
  });
});

describe("history status mapping", () => {
  it("maps machine statuses to user-facing Chinese labels", () => {
    expect(historyStatusLabel("done", 5)).toBe("完成");
    expect(historyStatusLabel("completed", 5)).toBe("完成");
    expect(historyStatusLabel("partial", 5)).toBe("部分结果");
    expect(historyStatusLabel("completed_with_pending", 5)).toBe("部分结果");
    // 017-US3: 未知状态不渲染标签（不再出现"失败但有 N 个岗位"）
    expect(historyStatusLabel("failed", 3)).toBe("");
    expect(historyStatusLabel("interrupted", 2)).toBe("");
    expect(historyStatusLabel("cancelled", 1)).toBe("");
  });

  it("labels round scopes without exposing platform names in all/history scopes", () => {
    expect(roundScopeLabel("all", "boss")).toBe("全部");
    expect(roundScopeLabel("history", "zhilian")).toBe("历史轮次");
    expect(roundScopeLabel("boss", "boss")).toBe("BOSS");
    expect(roundScopeLabel("zhilian", "zhilian")).toBe("智联");
  });
});

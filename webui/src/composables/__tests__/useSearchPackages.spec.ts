// Spec 044 B100：常用搜索配置包的领域逻辑测试。
// 只测"用户动作 → 请求 → 本地状态"的因果，不测页面渲染（组件测试负责）。
import { nextTick, ref, type Ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
    payload: Record<string, unknown> = {};
  },
  apiRequest: vi.fn(async () => ({})),
  // 与真实 api.ts 同口径：有 message 的 Error 用 message，否则用兜底文案。
  errorMessage: (error: unknown, fallback: string) => (
    error instanceof Error && error.message ? error.message : fallback
  ),
  userFacingMessage: (_error: unknown, fallback: string) => fallback,
  settingsApi: {},
}));

import { apiRequest } from "../../api";
import {
  defaultPackageName,
  useSearchPackages,
  type SearchPackageHooks,
  type SearchPackageRefs,
} from "../useSearchPackages";

const apiRequestMock = apiRequest as unknown as ReturnType<typeof vi.fn>;

function makeRefs(overrides: Partial<SearchPackageRefs> = {}): SearchPackageRefs {
  return {
    keywords: ref([{ word: "产品经理", recommended: true }]),
    selectedKeywords: ref(["产品经理"]),
    customKeyword: ref(""),
    cityText: ref("上海"),
    customCity: ref(""),
    profileSummary: ref("3 年 B 端产品经验"),
    profileFacts: ref({ experience_years: 3 }),
    ...overrides,
  };
}

function makeHooks() {
  return {
    persistDraft: vi.fn<NonNullable<SearchPackageHooks["persistDraft"]>>(),
    restoreDraft: vi.fn<() => void>(),
    restoreStep: vi.fn<() => void>(),
    enterSearchStep: vi.fn<NonNullable<SearchPackageHooks["enterSearchStep"]>>(),
    notify: vi.fn<NonNullable<SearchPackageHooks["notify"]>>(),
  };
}

function setup(overrides: Partial<SearchPackageRefs> = {}) {
  const refs = makeRefs(overrides);
  const hooks = makeHooks();
  const api = useSearchPackages(refs, hooks);
  return { refs, hooks, api };
}

function setupWithContext(
  context: { profileId: Ref<string>; roundKey: Ref<string>; analysisKey: Ref<number>; fileKey?: Ref<string> },
  overrides: Partial<SearchPackageRefs> = {},
) {
  const refs = makeRefs(overrides);
  const hooks = makeHooks();
  const api = useSearchPackages(refs, hooks, context);
  return { refs, hooks, api };
}

function packageBody(overrides: Record<string, unknown> = {}) {
  return {
    id: "pkg-1",
    name: "产品经理 · 上海",
    payloadVersion: 1,
    keywords: {
      candidates: [{ word: "产品经理", recommended: true }],
      selected: ["产品经理"],
      custom: "",
    },
    city: { text: "上海", custom: "" },
    profile: { summary: "3 年 B 端产品经验", facts: { experience_years: 3 } },
    createdAt: "2026-09-21T10:00:00+08:00",
    updatedAt: "2026-09-21T10:00:00+08:00",
    ...overrides,
  };
}

function apiFailure(code: string, message: string) {
  return Object.assign(new Error("请求失败"), {
    payload: { error: { code, message } },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  apiRequestMock.mockReset();
  apiRequestMock.mockResolvedValue({});
  localStorage.clear();
});

describe("useSearchPackages 保存（US1）", () => {
  it("首次保存 POST 当前第二页内容并记录当前包身份", async () => {
    const { api, refs, hooks } = setup();
    apiRequestMock.mockResolvedValueOnce(packageBody());
    await api.saveCurrent("我的配置");

    expect(apiRequestMock).toHaveBeenCalledTimes(1);
    const [path, options] = apiRequestMock.mock.calls[0];
    expect(path).toBe("/api/search-packages");
    expect(options.method).toBe("POST");
    expect(options.json.payloadVersion).toBe(1);
    expect(options.json.name).toBe("我的配置");
    expect(options.json.keywords.selected).toEqual(["产品经理"]);
    expect(options.json.city).toEqual({ text: "上海", custom: "" });
    expect(options.json.profile).toEqual({
      summary: "3 年 B 端产品经验",
      facts: { experience_years: 3 },
    });
    expect(api.currentPackageId.value).toBe("pkg-1");
    expect(api.currentPackageName.value).toBe("产品经理 · 上海");
    expect(refs.profileFacts.value).toEqual({ experience_years: 3 });
    expect(hooks.notify).toHaveBeenCalledWith("已保存常用配置", "success");
  });

  it("已有当前包时保存仍走 POST，并新增独立配置", async () => {
    const { api } = setup();
    apiRequestMock.mockResolvedValueOnce(packageBody());
    await api.saveCurrent("");
    apiRequestMock.mockResolvedValueOnce(packageBody({ id: "pkg-2", name: "改过的名字" }));
    await api.saveCurrent("改过的名字");

    const [path, options] = apiRequestMock.mock.calls[1];
    expect(path).toBe("/api/search-packages");
    expect(options.method).toBe("POST");
    expect(api.currentPackageId.value).toBe("pkg-2");
    expect(api.currentPackageName.value).toBe("改过的名字");
  });

  it("普通编辑第二页不触发任何配置包请求", async () => {
    const { refs, api } = setup();
    refs.keywords.value = [{ word: "改过的关键词", recommended: false }];
    refs.profileSummary.value = "改过的画像";
    await nextTick();
    expect(apiRequestMock).not.toHaveBeenCalled();
    expect(api.currentPackageId.value).toBeNull();
  });

  it("空名交给服务端生成默认名，本地默认名与规则一致", async () => {
    const { api } = setup();
    apiRequestMock.mockResolvedValueOnce(packageBody());
    await api.saveCurrent("");
    expect(apiRequestMock.mock.calls[0][1].json.name).toBe("");

    expect(defaultPackageName(
      { candidates: [], selected: ["产品经理"], custom: "" },
      { text: "上海", custom: "" },
    )).toBe("产品经理 · 上海");
    expect(defaultPackageName(
      { candidates: [], selected: [], custom: "" },
      { text: "", custom: "" },
    )).toBe("常用搜索配置");
    expect(defaultPackageName(
      { candidates: [{ word: "数据分析", recommended: true }], selected: [], custom: "" },
      { text: "杭州", custom: "" },
    )).toBe("数据分析 · 杭州");
  });

  it("保存失败发 error 通知且不改变当前包身份", async () => {
    const { api, hooks } = setup();
    apiRequestMock.mockRejectedValueOnce(apiFailure("invalid_package", "配置包内容必须是对象"));
    const result = await api.saveCurrent("坏配置");

    expect(result).toBeNull();
    expect(api.currentPackageId.value).toBeNull();
    expect(hooks.notify).toHaveBeenCalledWith("配置包内容必须是对象", "error");
    expect(api.saveBusy.value).toBe(false);
  });

  it("刷新/重挂载同一轮后恢复当前包身份，保存仍创建新包", async () => {
    const context = { profileId: ref("profile-1"), roundKey: ref("round-1"), analysisKey: ref(0) };
    const first = setupWithContext(context);
    apiRequestMock.mockResolvedValueOnce(packageBody());
    await first.api.selectPackage("pkg-1");

    apiRequestMock.mockClear();
    apiRequestMock.mockResolvedValueOnce(packageBody({
      city: { text: "深圳", custom: "南山" },
      profile: { summary: "恢复画像", facts: { experience_years: 5 } },
    }));
    const second = setupWithContext(context);
    await flushPromises();
    expect(second.api.currentPackageId.value).toBe("pkg-1");
    expect(second.refs.cityText.value).toBe("深圳");
    expect(second.refs.profileSummary.value).toBe("恢复画像");
    expect(apiRequestMock).toHaveBeenCalledWith("/api/search-packages/pkg-1");
    apiRequestMock.mockResolvedValueOnce(packageBody({ id: "pkg-2", name: "更新后的配置" }));
    await second.api.saveCurrent("更新后的配置");

    expect(apiRequestMock.mock.calls.at(-1)?.[0]).toBe("/api/search-packages");
    expect(apiRequestMock.mock.calls.at(-1)?.[1]?.method).toBe("POST");
    expect(second.api.currentPackageId.value).toBe("pkg-2");
  });

  it("重挂载配置包 GET 失败或不完整时不恢复身份，保存改走 POST", async () => {
    const context = { profileId: ref("profile-restore-fail"), roundKey: ref("round-1"), analysisKey: ref(0) };
    const first = setupWithContext(context);
    apiRequestMock.mockResolvedValueOnce(packageBody());
    await first.api.selectPackage("pkg-restore-fail");

    apiRequestMock.mockClear();
    apiRequestMock.mockRejectedValueOnce(apiFailure("package_unusable", "配置包读取失败"));
    const failed = setupWithContext(context);
    await flushPromises();
    expect(failed.api.currentPackageId.value).toBeNull();
    expect(localStorage.getItem("career-scout-search-package-identity:profile-restore-fail")).toBeNull();
    apiRequestMock.mockResolvedValueOnce(packageBody({ id: "pkg-created-after-failure" }));
    await failed.api.saveCurrent("重新保存");
    expect(apiRequestMock.mock.calls.at(-1)?.[0]).toBe("/api/search-packages");
    expect(apiRequestMock.mock.calls.at(-1)?.[1]?.method).toBe("POST");
  });

  it("重挂载配置包 GET 返回不完整 payload 时保持未绑定", async () => {
    const context = { profileId: ref("profile-restore-incomplete"), roundKey: ref("round-1"), analysisKey: ref(0) };
    const first = setupWithContext(context);
    apiRequestMock.mockResolvedValueOnce(packageBody());
    await first.api.selectPackage("pkg-restore-incomplete");

    apiRequestMock.mockClear();
    apiRequestMock.mockResolvedValueOnce(packageBody({ profile: { summary: "缺 facts" } }));
    const failed = setupWithContext(context);
    await flushPromises();
    expect(failed.api.currentPackageId.value).toBeNull();
    expect(localStorage.getItem("career-scout-search-package-identity:profile-restore-incomplete")).toBeNull();
  });

  it("重挂载恢复 GET 等待期间用户编辑第二页时不覆盖编辑内容", async () => {
    const context = { profileId: ref("profile-restore-edit"), roundKey: ref("round-1"), analysisKey: ref(0) };
    const first = setupWithContext(context);
    apiRequestMock.mockResolvedValueOnce(packageBody({ id: "pkg-restore-edit" }));
    await first.api.selectPackage("pkg-restore-edit");

    apiRequestMock.mockClear();
    const pending = deferred<ReturnType<typeof packageBody>>();
    apiRequestMock.mockReturnValueOnce(pending.promise);
    const restored = setupWithContext(context);
    restored.refs.keywords.value = [{ word: "用户关键词", recommended: false }];
    restored.refs.selectedKeywords.value = ["用户关键词"];
    restored.refs.customKeyword.value = "用户自定义词";
    restored.refs.cityText.value = "用户城市";
    restored.refs.customCity.value = "用户区域";
    restored.refs.profileSummary.value = "用户画像";
    restored.refs.profileFacts.value = { degree: "硕士", core_skills: ["用户技能"] };

    pending.resolve(packageBody({
      id: "pkg-restore-edit",
      city: { text: "旧城市", custom: "旧区域" },
      profile: { summary: "旧画像", facts: { experience_years: 9 } },
    }));
    await flushPromises();

    expect(restored.refs.keywords.value).toEqual([{ word: "用户关键词", recommended: false }]);
    expect(restored.refs.selectedKeywords.value).toEqual(["用户关键词"]);
    expect(restored.refs.customKeyword.value).toBe("用户自定义词");
    expect(restored.refs.cityText.value).toBe("用户城市");
    expect(restored.refs.customCity.value).toBe("用户区域");
    expect(restored.refs.profileSummary.value).toBe("用户画像");
    expect(restored.refs.profileFacts.value).toEqual({ degree: "硕士", core_skills: ["用户技能"] });
    expect(restored.api.currentPackageId.value).toBeNull();
    expect(localStorage.getItem("career-scout-search-package-identity:profile-restore-edit")).toBeNull();
    expect(restored.hooks.persistDraft).not.toHaveBeenCalled();
  });

  it("新轮次、切画像或新简历分析后清空旧包身份并改为 POST", async () => {
    const context = { profileId: ref("profile-1"), roundKey: ref("round-1"), analysisKey: ref(0) };
    const { api } = setupWithContext(context);
    apiRequestMock.mockResolvedValueOnce(packageBody());
    await api.selectPackage("pkg-1");

    context.roundKey.value = "round-2";
    await nextTick();
    expect(api.currentPackageId.value).toBeNull();
    apiRequestMock.mockResolvedValueOnce(packageBody({ id: "pkg-2" }));
    await api.saveCurrent("新轮配置");
    expect(apiRequestMock.mock.calls.at(-1)?.[0]).toBe("/api/search-packages");
    expect(apiRequestMock.mock.calls.at(-1)?.[1]?.method).toBe("POST");

    context.profileId.value = "profile-2";
    await nextTick();
    expect(api.currentPackageId.value).toBeNull();
    context.analysisKey.value += 1;
    await nextTick();
    expect(api.currentPackageId.value).toBeNull();
  });

  it("选择新的非空简历上下文会清空当前包，取消选择不重复触发失效", async () => {
    const context = {
      profileId: ref("profile-file"),
      roundKey: ref("round-1"),
      analysisKey: ref(0),
      fileKey: ref("old-resume.txt:10:1"),
    };
    const { api } = setupWithContext(context);
    apiRequestMock.mockResolvedValueOnce(packageBody());
    await api.selectPackage("pkg-1");
    expect(api.currentPackageId.value).toBe("pkg-1");

    context.fileKey.value = "";
    await nextTick();
    expect(api.currentPackageId.value).toBe("pkg-1");

    context.fileKey.value = "new-resume.txt:10:2";
    await nextTick();
    expect(api.currentPackageId.value).toBeNull();
  });

  it("上下文切换后丢弃尚未返回的配置包选择响应", async () => {
    const context = { profileId: ref("profile-stale"), roundKey: ref("round-1"), analysisKey: ref(0) };
    const pending = deferred<ReturnType<typeof packageBody>>();
    apiRequestMock.mockReturnValueOnce(pending.promise);
    const { api, hooks, refs } = setupWithContext(context);
    const selecting = api.selectPackage("pkg-stale");

    context.roundKey.value = "round-2";
    await nextTick();
    pending.resolve(packageBody({
      id: "pkg-stale",
      city: { text: "污染城市", custom: "" },
      profile: { summary: "污染画像", facts: { experience_years: 99 } },
    }));

    expect(await selecting).toBe(false);
    expect(refs.cityText.value).toBe("上海");
    expect(refs.profileSummary.value).toBe("3 年 B 端产品经验");
    expect(api.currentPackageId.value).toBeNull();
    expect(localStorage.getItem("career-scout-search-package-identity:profile-stale")).toBeNull();
    expect(hooks.persistDraft).not.toHaveBeenCalled();
    expect(hooks.enterSearchStep).not.toHaveBeenCalled();
  });

  it("上下文切换后丢弃尚未返回的首次保存 POST 响应", async () => {
    const context = { profileId: ref("profile-stale-post"), roundKey: ref("round-1"), analysisKey: ref(0) };
    const pending = deferred<ReturnType<typeof packageBody>>();
    apiRequestMock.mockReturnValueOnce(pending.promise);
    const { api } = setupWithContext(context);
    const saving = api.saveCurrent("旧上下文");

    context.profileId.value = "profile-new";
    await nextTick();
    pending.resolve(packageBody({ id: "pkg-stale-post" }));

    expect(await saving).toBeNull();
    expect(api.currentPackageId.value).toBeNull();
    expect(api.packages.value).toEqual([]);
    expect(localStorage.getItem("career-scout-search-package-identity:profile-new")).toBeNull();
  });

  it("上下文切换后丢弃过期保存错误，不向新上下文报告旧请求失败", async () => {
    const context = { profileId: ref("profile-stale-error"), roundKey: ref("round-1"), analysisKey: ref(0) };
    const pending = deferred<ReturnType<typeof packageBody>>();
    apiRequestMock.mockReturnValueOnce(pending.promise);
    const { api, hooks } = setupWithContext(context);
    const saving = api.saveCurrent("旧上下文");

    context.roundKey.value = "round-2";
    await nextTick();
    pending.reject(apiFailure("persistence_failed", "旧请求错误"));

    expect(await saving).toBeNull();
    expect(hooks.notify).not.toHaveBeenCalled();
    expect(api.currentPackageId.value).toBeNull();
  });

  it("上下文切换后丢弃尚未返回的保存 POST 响应", async () => {
    const context = { profileId: ref("profile-stale-save"), roundKey: ref("round-1"), analysisKey: ref(0) };
    const pending = deferred<ReturnType<typeof packageBody>>();
    apiRequestMock.mockReturnValueOnce(pending.promise);
    const { api } = setupWithContext(context);
    const saving = api.saveCurrent("旧上下文副本");

    context.analysisKey.value = 1;
    await nextTick();
    pending.resolve(packageBody({ id: "pkg-stale-save" }));

    expect(await saving).toBeNull();
    expect(api.currentPackageId.value).toBeNull();
    expect(api.packages.value).toEqual([]);
  });

  it("上下文切换后丢弃尚未返回的保存 POST 响应", async () => {
    const context = { profileId: ref("profile-stale-save-post"), roundKey: ref("round-1"), analysisKey: ref(0) };
    const { api } = setupWithContext(context);
    apiRequestMock.mockResolvedValueOnce(packageBody({ id: "pkg-existing" }));
    await api.selectPackage("pkg-existing");
    apiRequestMock.mockClear();

    const pending = deferred<ReturnType<typeof packageBody>>();
    apiRequestMock.mockReturnValueOnce(pending.promise);
    const saving = api.saveCurrent("旧上下文保存");
    context.roundKey.value = "round-2";
    await nextTick();
    pending.resolve(packageBody({ id: "pkg-existing", name: "污染更新" }));

    expect(await saving).toBeNull();
    expect(api.currentPackageId.value).toBeNull();
    expect(api.packages.value).toEqual([]);
  });
});

describe("useSearchPackages 列表与选择（US2）", () => {
  it("加载列表请求配置包列表接口", async () => {
    const { api } = setup();
    apiRequestMock.mockResolvedValueOnce({
      items: [
        { id: "p1", name: "产品经理 · 上海", createdAt: "a", updatedAt: "b" },
      ],
    });
    const ok = await api.loadList();

    expect(ok).toBe(true);
    expect(apiRequestMock).toHaveBeenCalledWith("/api/search-packages");
    expect(api.packages.value.map((item) => item.id)).toEqual(["p1"]);
    expect(api.listBusy.value).toBe(false);
  });

  it("列表加载失败保留可重试状态并发 error 通知", async () => {
    const { api, hooks } = setup();
    apiRequestMock.mockRejectedValueOnce(apiFailure("persistence_failed", "常用配置加载失败，请重试"));
    const ok = await api.loadList();

    expect(ok).toBe(false);
    expect(api.listError.value).toBe("常用配置加载失败，请重试");
    expect(hooks.notify).toHaveBeenCalledWith("常用配置加载失败，请重试", "error");
    expect(api.listBusy.value).toBe(false);
  });

  it("选择有效配置包：完整回填第二页、写共享草稿、最后切页", async () => {
    const { api, hooks, refs } = setup();
    apiRequestMock.mockResolvedValueOnce(packageBody({
      id: "pkg-9",
      name: "运营 · 深圳",
      keywords: {
        candidates: [{ word: "运营", recommended: false }],
        selected: ["运营", "社群"],
        custom: "待定词",
      },
      city: { text: "深圳", custom: "南山" },
      profile: { summary: "5 年运营", facts: { experience_years: 5, core_skills: ["社群"] } },
    }));
    const ok = await api.selectPackage("pkg-9");

    expect(ok).toBe(true);
    expect(refs.keywords.value).toEqual([{ word: "运营", recommended: false }]);
    expect(refs.selectedKeywords.value).toEqual(["运营", "社群"]);
    expect(refs.customKeyword.value).toBe("待定词");
    expect(refs.cityText.value).toBe("深圳");
    expect(refs.customCity.value).toBe("南山");
    expect(refs.profileSummary.value).toBe("5 年运营");
    expect(refs.profileFacts.value).toEqual({ experience_years: 5, core_skills: ["社群"] });
    expect(api.currentPackageId.value).toBe("pkg-9");
    expect(api.currentPackageName.value).toBe("运营 · 深圳");
    expect(hooks.notify).toHaveBeenCalledWith("已使用常用配置", "success");
    // 顺序：先写共享草稿，最后才切页。
    expect(hooks.persistDraft.mock.invocationCallOrder[0])
      .toBeLessThan(hooks.enterSearchStep.mock.invocationCallOrder[0]);
    // 选择不写库、不触发任何写请求（保存必须由用户显式点击）。
    expect(apiRequestMock.mock.calls.every(([, options]) => !options || !options.method)).toBe(true);
  });

  it("服务端判为不可用时保持第一页且不回填", async () => {
    const { api, hooks, refs } = setup();
    apiRequestMock.mockRejectedValueOnce(apiFailure("package_unusable", "这套配置无法完整读取，请重新保存"));
    const ok = await api.selectPackage("pkg-bad");

    expect(ok).toBe(false);
    expect(refs.cityText.value).toBe("上海");
    expect(refs.profileSummary.value).toBe("3 年 B 端产品经验");
    expect(hooks.enterSearchStep).not.toHaveBeenCalled();
    expect(hooks.persistDraft).not.toHaveBeenCalled();
    expect(hooks.notify).toHaveBeenCalledWith("这套配置无法完整读取，请重新保存", "error");
  });

  it("本地复核发现整包不完整时整次选择失败", async () => {
    const { api, hooks, refs } = setup();
    apiRequestMock.mockResolvedValueOnce(packageBody({
      profile: { summary: "画像" },
    }));
    const ok = await api.selectPackage("pkg-incomplete");

    expect(ok).toBe(false);
    expect(refs.profileSummary.value).toBe("3 年 B 端产品经验");
    expect(hooks.enterSearchStep).not.toHaveBeenCalled();
    expect(hooks.notify).toHaveBeenCalledWith("这套配置无法完整读取，请重新保存", "error");
  });

  it("保存与加载均拒绝画像事实中的平台、第三页和原始分析数据", async () => {
    const { api, refs, hooks } = setup({
      profileFacts: ref({
        core_skills: ["Python"],
        platform: "boss",
        filterValues: { salary: ["20-30K"] },
        resumeAnalysis: { fields: { keyword: ["Python"] } },
        employment_history: [{ company: "甲公司", districtCode: "310104" }],
      }),
    });
    apiRequestMock.mockResolvedValueOnce(packageBody({
      profile: {
        summary: "画像",
        facts: {
          core_skills: ["Python"],
          platform: "boss",
          filterValues: { salary: ["20-30K"] },
        },
      },
    }));

    await api.saveCurrent("画像边界");
    const payload = apiRequestMock.mock.calls[0][1].json.profile.facts;
    expect(payload).toEqual({ core_skills: ["Python"], employment_history: [{ company: "甲公司" }] });

    apiRequestMock.mockResolvedValueOnce(packageBody({
      profile: { summary: "画像", facts: { core_skills: ["Python"], platform: "boss" } },
    }));
    const selected = await api.selectPackage("pkg-1");
    expect(selected).toBe(false);
    expect(refs.profileFacts.value).toEqual(expect.objectContaining({ platform: "boss" }));
    expect(hooks.notify).toHaveBeenCalledWith("这套配置无法完整读取，请重新保存", "error");
  });

  it("保存与加载保留画像契约中的学历字段", async () => {
    const facts = {
      degree: "本科",
      degree_type: "统招",
      graduation_date: "2024-06",
      graduation_year: 2024,
      experience_years: 3,
      experience_years_source: "resume_explicit",
    };
    const { api, refs } = setup({ profileFacts: ref(facts) });
    apiRequestMock.mockResolvedValueOnce(packageBody({
      profile: { summary: "画像", facts },
    }));

    await api.saveCurrent("学历画像");
    expect(apiRequestMock.mock.calls[0][1].json.profile.facts).toEqual(facts);

    apiRequestMock.mockResolvedValueOnce(packageBody({
      profile: { summary: "画像", facts },
    }));
    expect(await api.selectPackage("pkg-1")).toBe(true);
    expect(refs.profileFacts.value).toEqual(facts);
  });

  it("重命名只改名称并就地更新列表与当前名称", async () => {
    const { api, refs } = setup();
    apiRequestMock.mockResolvedValueOnce({ items: [
      { id: "p1", name: "原名", createdAt: "a", updatedAt: "b" },
    ] });
    await api.loadList();
    apiRequestMock.mockResolvedValueOnce(packageBody({ id: "p1", name: "改过的名字" }));
    await api.selectPackage("p1");
    refs.cityText.value = "深圳";

    apiRequestMock.mockResolvedValueOnce(packageBody({ id: "p1", name: "新名字" }));
    const ok = await api.renamePackage("p1", "  新名字  ");

    expect(ok).toBe(true);
    const [path, options] = apiRequestMock.mock.calls[2];
    expect(path).toBe("/api/search-packages/p1/name");
    expect(options.method).toBe("PATCH");
    expect(options.json).toEqual({ name: "新名字" });
    expect(api.packages.value[0].name).toBe("新名字");
    expect(api.currentPackageName.value).toBe("新名字");
    expect(refs.cityText.value).toBe("深圳");
  });

  it("重命名失败发 error 通知且列表保持原样", async () => {
    const { api, hooks } = setup();
    apiRequestMock.mockResolvedValueOnce({ items: [
      { id: "p1", name: "原名", createdAt: "a", updatedAt: "b" },
    ] });
    await api.loadList();
    apiRequestMock.mockRejectedValueOnce(apiFailure("invalid_name", "配置名称长度必须为 1 至 80 个字符"));
    const ok = await api.renamePackage("p1", "");

    expect(ok).toBe(false);
    expect(api.packages.value[0].name).toBe("原名");
    expect(hooks.notify).toHaveBeenCalledWith("配置名称长度必须为 1 至 80 个字符", "error");
    expect(api.manageBusy.value).toBe(false);
  });

  it("删除当前包只清当前包身份，不清当前轮已回填内容", async () => {
    const { api, refs } = setup();
    apiRequestMock.mockResolvedValueOnce(packageBody({ id: "p1" }));
    await api.selectPackage("p1");
    expect(api.currentPackageId.value).toBe("p1");

    apiRequestMock.mockResolvedValueOnce("");
    const ok = await api.deletePackage("p1");

    expect(ok).toBe(true);
    expect(api.currentPackageId.value).toBeNull();
    expect(api.currentPackageName.value).toBe("");
    expect(refs.profileSummary.value).toBe("3 年 B 端产品经验");
    // 后续保存必须按首次保存创建，而不是 PUT 一个已删除的 id。
    apiRequestMock.mockResolvedValueOnce(packageBody({ id: "p2", name: "重建的配置" }));
    await api.saveCurrent("");
    const [path, options] = apiRequestMock.mock.calls[2];
    expect(path).toBe("/api/search-packages");
    expect(options.method).toBe("POST");
    expect(api.currentPackageId.value).toBe("p2");
  });

  it("删除失败发 error 通知且目标仍在列表", async () => {
    const { api, hooks } = setup();
    apiRequestMock.mockResolvedValueOnce({ items: [
      { id: "p1", name: "保留", createdAt: "a", updatedAt: "b" },
    ] });
    await api.loadList();
    apiRequestMock.mockRejectedValueOnce(apiFailure("persistence_failed", "删除失败，请重试"));
    const ok = await api.deletePackage("p1");

    expect(ok).toBe(false);
    expect(api.packages.value).toHaveLength(1);
    expect(hooks.notify).toHaveBeenCalledWith("删除失败，请重试", "error");
  });

  it("提交期异常回滚到应用前的第二页内容", async () => {
    const { api, hooks, refs } = setup();
    hooks.persistDraft.mockImplementation(() => {
      throw new Error("草稿写入失败");
    });
    apiRequestMock.mockResolvedValueOnce(packageBody({
      id: "pkg-rollback",
      city: { text: "广州", custom: "" },
      profile: { summary: "广州画像", facts: { experience_years: 9 } },
    }));
    const ok = await api.selectPackage("pkg-rollback");

    expect(ok).toBe(false);
    expect(refs.cityText.value).toBe("上海");
    expect(refs.profileSummary.value).toBe("3 年 B 端产品经验");
    expect(refs.profileFacts.value).toEqual({ experience_years: 3 });
    expect(api.currentPackageId.value).toBeNull();
    expect(hooks.enterSearchStep).not.toHaveBeenCalled();
    expect(hooks.notify).toHaveBeenCalledWith("草稿写入失败", "error");
  });

  it("切页提交异常时回滚共享草稿、页面 refs 与持久化当前包身份", async () => {
    const context = { profileId: ref("profile-rollback"), roundKey: ref("round-1"), analysisKey: ref(0) };
    const first = setupWithContext(context);
    apiRequestMock.mockResolvedValueOnce(packageBody({
      id: "pkg-old",
      name: "旧配置",
      city: { text: "上海", custom: "" },
      profile: { summary: "旧画像", facts: { experience_years: 3 } },
    }));
    await first.api.selectPackage("pkg-old");
    const { api, hooks, refs } = first;

    apiRequestMock.mockResolvedValueOnce(packageBody({
      id: "pkg-new",
      name: "新配置",
      city: { text: "广州", custom: "" },
      profile: { summary: "新画像", facts: { experience_years: 9 } },
    }));
    hooks.enterSearchStep.mockImplementationOnce(() => {
      throw new Error("切页失败");
    });

    expect(await api.selectPackage("pkg-new")).toBe(false);
    expect(refs.cityText.value).toBe("上海");
    expect(refs.profileSummary.value).toBe("旧画像");
    expect(refs.profileFacts.value).toEqual({ experience_years: 3 });
    expect(api.currentPackageId.value).toBe("pkg-old");
    expect(JSON.parse(localStorage.getItem("career-scout-search-package-identity:profile-rollback") || "{}"))
      .toMatchObject({ id: "pkg-old", roundKey: "round-1" });
    expect(hooks.restoreDraft).toHaveBeenCalledTimes(1);
    expect(hooks.restoreStep).toHaveBeenCalledTimes(1);
    expect(hooks.notify).toHaveBeenCalledWith("切页失败", "error");
  });
});

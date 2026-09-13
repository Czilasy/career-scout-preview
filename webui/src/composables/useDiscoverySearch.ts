// 021 B8 T027：DiscoveryView search 动作层（自 DiscoveryView.vue script 原样搬运，函数体零改动，跨域引用经 deps 调用时解析）。
// 031 B8：deps 形参类型 = discoveryDeps.ts 的 SearchNeeds（跨域依赖契约）。
import type { Ref } from "vue";
import type { DiscoveryState } from "./useDiscoveryState";
import type { SearchNeeds } from "./discoveryDeps";
import { computed, nextTick, watch } from "vue";
import type {
  AdvancedSettingsState,
  CandidateProfile,
  ExecutionSelection,
  ExecutionSettings,
  FrozenSearchScope,
  JobItem,
  LocationCondition,
  Notice,
  Platform,
  PlatformCityCatalog,
  PlatformFilterSchema,
  RoundContext,
  TaskSnapshot as ApiTaskSnapshot,
} from "../types";
import { ApiError, apiRequest, errorMessage, settingsApi, userFacingMessage } from "../api";
import {
  buildSearchScriptParams,
  createCityCatalogLoader,
  createPlatformState,
  createSchemaLoader,
  DEFAULT_PLATFORM,
  filterPipelineResultByPlatform,
  normalizeScopePreview,
  isNationwideCityName,
  partitionPipelineResult,
  projectResumeSuggestionToSchema,
  shouldConfirmNationalScope,
  singleSelectNextValue,
} from "../discovery";
import { setThemePlatform } from "../composables/useTheme";
import { liveTaskStep } from "./useDiscoveryState";
import {
  useResumeAnalysisFlow,
  type ResumeAnalysisTaskState,
} from "./useResumeAnalysisFlow";
import type { AnalyzeResponse } from "./useDiscoveryState";

export function useDiscoverySearch(state: DiscoveryState, deps: SearchNeeds) {
  const { LOGIN_ERROR_CODES, SPEED_FIELDS, activeCategory, activeStep, advancedBusy, advancedRanges, advancedSettings, aiConsent, analysisReady, appliedResumePlatforms, autoScreenArmed, cityCatalogBusy, cityCatalogRef, cityList, cityLoader, cityText, currentRoundStatus, customCity, customKeyword, draftPlatform, draftPlatformDisabled, dragActive, executionSelection, fieldLabels, filterGroups, filterValues, finishedPartial, historyBackToLatest, historyRound, interruptedRunId, keywords, locationDraft, loginGuide, mirrorSearchDraftKeywords, nationalScopeConfirm, oneClickOpen, pagesValue, pausedRunId, pendingPlatformSwitch, pipelineResult, pipelineResultRunId, platformState, profileConfirmed, profileError, profileFacts, profileInputEl, profileSummary, recrawlPlatformGuide, recrawlSnapshot, recrawlTaskId, rejectedIds, restoredTaskHint, resultLoaded, resultPlatformFilter, resultRunIds, resumeAnalysis, resumeAnalysisLandOnReturn, resumeAnalysisPhase, resumeAnalysisReset, resumeAnalysisRestore, resumeError, schemaBusy, schemaLoader, schemaRef, scopePreview, scopePreviewBusy, scopePreviewReqId, setScopePreviewFor, scrapeCompleted, scrapeSnapshot, scrapeTaskId, screenBusy, screenSnapshot, screenTaskId, selectedFile, selectedKeywords, uploadBusy } = state;
  const { cancelActiveTasksForNewRound, clearLatestResult, enterSearchStep, notify, openOneClickDialog, props, restoreRunningTask, startScrape } = deps;
  // 画像框自动高度（含页面二高度现场）由 useProfileInputScene 统一接线：
  // 内容/可见性变化都会触发重算并回写现场，这里不再单独调度。


async function showLoginGuide(platform: Platform) {
  loginGuide.value = { visible: true, platform, accountName: "" };
  try {
    const data = await apiRequest<{
      accounts?: { id: string; name: string }[];
      active_account?: string;
    }>("/api/browser-accounts");
    const active = data.active_account || "a";
    const account = (data.accounts || []).find((item) => item.id === active);
    // 内置 ~/.career-scout/chrome-profile 账号在 UI 上固定叫「默认账号」（D7）。
    let accountName = active;
    if (account) accountName = account.id === "a" ? "默认账号" : account.name;
    loginGuide.value.accountName = accountName;
  } catch {
    // 账号名只是引导文案辅助，拉不到就显示通用文案。
  }
}

// T503：平台三身份独立（platform-schema.md L142-159）
// platformState 是真相之源（非响应式闭包）；draftPlatform 是 Vue 镜像，仅供模板渲染。
// setDraftPlatform 同步更新两者，不用 watcher 相互覆盖（不变式 4）。
// 仅切换新任务草稿，不改 task/result（不变式 1）；T505 起按 platformState.draft 加载 schema/城市。


function isLoginErrorCode(code: unknown): boolean {
  return typeof code === "string" && LOGIN_ERROR_CODES.has(code);
}


function confirmNationalScope() {
  const action = nationalScopeConfirm.value;
  nationalScopeConfirm.value = null;
  if (action === "scrape") void deps.startScrape();
  if (action === "one-click") deps.openOneClickDialog();
}


function cancelNationalScope() {
  nationalScopeConfirm.value = null;
}


function setDraftPlatform(platform: Platform) {
  if (platformState.draft === platform) return;
  platformState.setDraftPlatform(platform);
  draftPlatform.value = platform;
  // 同步主题品牌色到新平台（boss 青 / 智联蓝）。
  setThemePlatform(platform);
  // B007：切平台视为新草稿，清掉旧 run 身份与 scope 快照；已加载的最新轮结果保留。
  scopePreview.value = null;
  scopePreviewBusy.value = false;
  scrapeTaskId.value = "";
  scrapeCompleted.value = false;
  scrapeSnapshot.value = null;
  screenTaskId.value = "";
  screenSnapshot.value = null;
  interruptedRunId.value = "";
  restoredTaskHint.value = "";
  // 切换草稿平台后按新平台重新加载 schema / 城市；旧请求被 loader 内部取消丢弃。
  oneClickOpen.value = false;
  autoScreenArmed.value = false;
  void loadFilterLabels();
  void loadCityCatalog();
}


function requestDraftPlatform(platform: Platform) {
  if (platformState.draft === platform) return;
  // 已抓完但尚未生成第四页结果的轮次只存在临时抓取上下文，切换会清掉它。
  // 先征得确认，取消时不改变草稿平台或任何任务状态。
  if (scrapeCompleted.value && !resultLoaded.value && !screenBusy.value) {
    pendingPlatformSwitch.value = platform;
    return;
  }
  setDraftPlatform(platform);
}


function cancelPlatformSwitch() {
  pendingPlatformSwitch.value = null;
}


function confirmPlatformSwitch() {
  const platform = pendingPlatformSwitch.value;
  pendingPlatformSwitch.value = null;
  if (platform) setDraftPlatform(platform);
}

// T505/T509：按指定平台加载 schema（/api/filter-labels?platform=）。
// schemaLoader 内部用单调 reqId + AbortController + 响应平台校验，
// 保证旧平台响应晚到不覆盖当前平台（platform-schema.md L151-156）。
// T509：默认参数 = 草稿平台（新任务表单/简历建议路径）；deps.restoreRunningTask 显式传入任务平台
// 以满足 platform-schema.md L157「先从任务响应设置任务平台，再加载对应 schema/城市」。
let schemaLoadToken = 0;

async function loadFilterLabels(platform: Platform = platformState.draft) {
  if (schemaLoader.loadedPlatform === platform && schemaRef.value) return;
  const requestToken = ++schemaLoadToken;
  schemaBusy.value = true;
  try {
    const accepted = await schemaLoader.load(platform, (p, signal) =>
      apiRequest<PlatformFilterSchema>(
        `/api/filter-labels?platform=${encodeURIComponent(p)}`,
        { signal },
      ),
    );
    if (accepted && schemaLoader.data) {
      schemaRef.value = schemaLoader.data;
    }
  } catch { /* non-critical：loader 已记录 error */ }
  finally {
    if (requestToken === schemaLoadToken) schemaBusy.value = false;
  }
}

// T505/T509：按指定平台加载城市目录（/api/options?platform=）。
// 与 loadFilterLabels 共用同一份序号 + 取消 + 校验逻辑（createAsyncResourceLoader）。

// T505/T509：按指定平台加载城市目录（/api/options?platform=）。
// 与 loadFilterLabels 共用同一份序号 + 取消 + 校验逻辑（createAsyncResourceLoader）。
async function loadCityCatalog(platform: Platform = platformState.draft) {
  if (cityLoader.loadedPlatform === platform && cityCatalogRef.value) return;
  cityCatalogBusy.value = true;
  try {
    const accepted = await cityLoader.load(platform, (p, signal) =>
      apiRequest<PlatformCityCatalog>(
        `/api/options?platform=${encodeURIComponent(p)}`,
        { signal },
      ),
    );
    if (accepted && cityLoader.data) {
      cityCatalogRef.value = cityLoader.data;
    }
  } catch { /* non-critical：loader 已记录 error */ }
  finally {
    if (cityLoader.pendingPlatform === null) cityCatalogBusy.value = false;
  }
}


function confirmCities() {
  const cities = cityList.value;
  if (!cities.length) {
    deps.notify("请输入至少一个城市", "warning");
    return;
  }
  deps.notify(`已确认 ${cities.length} 个城市：${cities.join("、")}`, "success");
}


function addCustomCity() {
  const city = customCity.value.trim().replace(/[，,]+$/, "");
  if (!city) return;
  if (isNationwideCityName(city)) {
    // 「全国」不是城市：不选城市就是全国范围，不把它写进城市草稿，
    // 否则范围预览会把它当城市名发出去、被后端拒绝（挡新一轮）。
    customCity.value = "";
    deps.notify("「全国」不用填——不选城市就是全国范围", "warning");
    return;
  }
  if (cityList.value.includes(city)) {
    customCity.value = "";
    return;
  }
  const current = cityText.value.trim().replace(/[，,]+$/, "");
  cityText.value = current ? `${current},${city}` : city;
  customCity.value = "";
}


function removeCity(city: string) {
  cityText.value = cityList.value.filter((c) => c !== city).join(",");
  locationDraft.clearLocations(draftPlatform.value, city);
}


function toggleFilter(key: string, code: string) {
  const drafts = filterValues.value[draftPlatform.value];
  const values = drafts[key] || [];
  // 028：单选字段（第 7 类招聘者上次活跃）点新值替换、点已选值取消。
  const group = filterGroups.value.find((group) => group.key === key);
  const single = singleSelectNextValue(group?.multiple, values, code);
  if (single !== null) {
    drafts[key] = single;
    return;
  }
  drafts[key] = values.includes(code)
    ? values.filter((value) => value !== code)
    : [...values, code];
}


function chooseFile(event: Event) {
  const input = event.target as HTMLInputElement;
  selectedFile.value = input.files?.[0] || null;
}


function handleDrop(event: DragEvent) {
  dragActive.value = false;
  selectedFile.value = event.dataTransfer?.files?.[0] || null;
}


const resumeFlow = useResumeAnalysisFlow({
  refs: {
    activeStep,
    uploadBusy,
    resumeError,
    resumeAnalysis,
  },
  api: {
    postAnalyzeResume: (form) => apiRequest<AnalyzeResponse>("/api/analyze-resume", {
      method: "POST",
      body: form,
    }),
    fetchTaskState: (taskId) => apiRequest<ResumeAnalysisTaskState>(
      // Spec041 返工：任务状态查询必须带当前画像，跨画像读一律按不存在处理。
      `/api/task-state/${encodeURIComponent(taskId)}`
      + (props.profileId ? `?profile_id=${encodeURIComponent(props.profileId)}` : ""),
    ),
    cancelActiveTasksForNewRound: () => deps.cancelActiveTasksForNewRound(),
    clearLatestResult: () => deps.clearLatestResult(),
    enterSearchStep: () => deps.enterSearchStep(),
    notify: (message, tone) => deps.notify(message, tone),
  },
  onAnalysisSuccess: (raw) => {
    const data = raw as AnalyzeResponse;
    const viewingHistory = Boolean(historyRound.value);
    if (!viewingHistory) historyBackToLatest();
    scrapeTaskId.value = "";
    screenTaskId.value = "";
    recrawlTaskId.value = "";
    scrapeSnapshot.value = null;
    screenSnapshot.value = null;
    recrawlSnapshot.value = null;
    if (!viewingHistory) {
      historyRound.value = null;
      pipelineResultRunId.value = "";
      resultPlatformFilter.value = "all";
      finishedPartial.value = false;
    }
    recrawlPlatformGuide.value = null;
    if (!viewingHistory) resultRunIds.value = { boss: "", zhilian: "" };
    pausedRunId.value = "";
    interruptedRunId.value = "";
    restoredTaskHint.value = "";
    scopePreview.value = null;
    autoScreenArmed.value = false;
    locationDraft.reset();
    oneClickOpen.value = false;
    scopePreviewBusy.value = false;
    if (!viewingHistory) {
      currentRoundStatus.value = "";
      activeCategory.value = "matched";
    }
    initializeFromAnalysis(data);
    analysisReady.value = true;
    scrapeCompleted.value = false;
    if (!viewingHistory) {
      resultLoaded.value = false;
      pipelineResult.value = null;
      rejectedIds.value = new Set();
    }
    deps.notify("简历分析完成，请确认关键词与城市", "success");
  },
});

watch(resumeFlow.phase, (phase) => {
  resumeAnalysisPhase.value = phase;
});
resumeAnalysisLandOnReturn.value = () => {
  resumeFlow.landOnReturn();
  // 结果域可能在同一个事件循环里立即读取落点，不能等 watch 的下一拍。
  resumeAnalysisPhase.value = resumeFlow.phase.value;
};
resumeAnalysisReset.value = resumeFlow.reset;
resumeAnalysisRestore.value = resumeFlow.restore;

function analyzeResume() {
  resumeError.value = "";
  const file = selectedFile.value;
  if (!file) {
    deps.notify("请先选择简历文件", "warning");
    return;
  }
  if (!aiConsent.value) {
    deps.notify("请勾选 AI 解析同意后再继续", "warning");
    return;
  }
  // 035：未结束任务存在时，上传简历不取消旧任务、不开新一轮，直接跳回任务视图。
  // 跳回落点按任务类型分派（抓取活 → 02；筛选/重抓活 → 03）。
  const liveStep = liveTaskStep(state);
  if (liveStep) {
    activeStep.value = liveStep;
    deps.notify("当前还有任务在跑，已回到任务进度", "warning");
    return;
  }
  resumeFlow.startAnalysis({
    file,
    platform: draftPlatform.value,
    aiConsent: aiConsent.value,
    profileId: props.profileId,
  });
}


function initializeFromAnalysis(data: AnalyzeResponse) {
  const fields = data.fields || {};
  // T507：不替换权威标签 fieldLabels（platform-schema.md L147）。
  // filterGroups 由 schemaLoader 加载的 schema 驱动，不用 analyze 响应的 labels 覆盖。
  // data.labels 仍保留给 fallback 或后续调试，但不写入 fieldLabels。
  const rawKeywords = Array.isArray(fields.keyword) ? fields.keyword : [];
  keywords.value = rawKeywords
    .map((item) => typeof item === "string"
      ? { word: item, recommended: false }
      : {
        word: String((item as Record<string, unknown>).word || ""),
        recommended: Boolean((item as Record<string, unknown>).recommended),
      })
    .filter((item) => item.word)
    .sort((a, b) => Number(b.recommended) - Number(a.recommended));
  const recommended = keywords.value.filter((item) => item.recommended).map((item) => item.word);
  selectedKeywords.value = recommended.length ? recommended : keywords.value.map((item) => item.word);
  // Spec041 返工：分析结果是平台无关的建议，写入两个平台槽位；用户随后在
  // 各平台的增删仍只影响该平台（真实验收失败项三：切平台不丢也不串）。
  mirrorSearchDraftKeywords();
  // 城市由用户选择，AI 不代填；未选择时默认全国。
  cityText.value = "";
  // T507：按当前已加载 schema 投影筛选建议（platform-schema.md L147）。
  // 只接受 schema 允许的字段；boss.stage 与 zhilian.company_nature 因 schema 不同不会串用。
  // 若 schema 未加载（如刚切平台尚未响应），保留空草稿，不投影。
  // B009：保存中文语义，切平台时按新 schema 重新投影，不静默丢字段。
  resumeAnalysis.value = data;
  appliedResumePlatforms.value = new Set();
  filterValues.value = { boss: {}, zhilian: {} };
  applyResumeAnalysisToCurrentSchema();
  profileSummary.value = String(fields.profile_summary || "");
  const pfacts = (fields as Record<string, unknown>).profile_facts;
  profileFacts.value = (pfacts && typeof pfacts === "object"
    ? pfacts as Record<string, unknown> : {});
}


function applyResumeAnalysisToCurrentSchema() {
  const analysis = resumeAnalysis.value;
  const schema = schemaRef.value;
  if (!analysis || !schema || schema.platform !== draftPlatform.value) return;
  if (appliedResumePlatforms.value.has(draftPlatform.value)) return;
  const semantic = analysis.semantic;
  const semanticForSchema = semantic
    ? {
      ...semantic,
      recruiter_activity: semantic.recruiter_activity
        || semantic.recruiterActivity
        || semantic["招聘者活跃时间"]
        || [],
    }
    : undefined;
  const projected = semanticForSchema
    ? projectResumeSuggestionToSchema(semanticForSchema, schema)
    : {};
  // 第七类是单选档位：兼容分析服务返回中文档位，也兼容已经返回稳定码的结果。
  const activityField = schema.fields.find((field) => field.key === "recruiter_activity");
  const activityValues = semanticForSchema?.recruiter_activity || [];
  if (activityField && activityValues.length) {
    const activityCodes = activityValues
      .map((value) => activityField.options.find((option) => option.label === value || option.value === value)?.value)
      .filter((value): value is string => Boolean(value));
    if (activityCodes.length) projected.recruiter_activity = activityCodes;
  }
  if (!semanticForSchema) {
    // 旧响应兜底：直接按当前 schema 校验 code。
    for (const field of schema.fields) {
      const value = analysis.fields[field.key];
      let codesRaw: unknown[] = [];
      if (Array.isArray(value)) codesRaw = value;
      else if (value) codesRaw = [value];
      const codes = codesRaw
        .map(String)
        .filter((code) => code !== "0" && field.options.some((opt) => opt.value === code));
      if (codes.length) projected[field.key] = codes;
    }
  }
  filterValues.value[draftPlatform.value] = projected;
  appliedResumePlatforms.value = new Set([...appliedResumePlatforms.value, draftPlatform.value]);
}


function toggleKeyword(word: string) {
  selectedKeywords.value = selectedKeywords.value.includes(word)
    ? selectedKeywords.value.filter((item) => item !== word)
    : [...selectedKeywords.value, word];
}


function removeKeyword(word: string) {
  keywords.value = keywords.value.filter((item) => item.word !== word);
  selectedKeywords.value = selectedKeywords.value.filter((item) => item !== word);
}


function addCustomKeyword() {
  const word = customKeyword.value.trim().replace(/[，,]+$/, "");
  if (!word) return;
  if (!keywords.value.some((item) => item.word === word)) {
    keywords.value.push({ word, recommended: false });
  }
  if (!selectedKeywords.value.includes(word)) selectedKeywords.value.push(word);
  customKeyword.value = "";
}


function confirmProfile() {
  if (profileConfirmed.value) return;
  if (!validateProfileForScreen()) {
    deps.notify("求职画像至少 10 个字（不含首尾空格）", "warning");
    return;
  }
  profileConfirmed.value = true;
}


function handleProfileInput() {
 if (profileError.value && profileSummary.value.trim().length >= 10) profileError.value = "";
}


function handleProfileBlur() {
  if (profileSummary.value.trim().length < 10) {
    profileError.value = "求职画像至少 10 个字（不含首尾空格）";
  } else {
    profileError.value = "";
  }
}


function validateProfileForScreen(): boolean {
 if (profileSummary.value.trim().length < 10) {
   profileError.value = "求职画像至少 10 个字（不含首尾空格）";
   void nextTick(() => profileInputEl.value?.focus());
   return false;
 }
 profileError.value = "";
 return true;
}


function requireProfileConfirmed(): boolean {
  if (profileConfirmed.value) return true;
  deps.notify("确认后 AI 精筛按当前画像判断，修改画像需重新确认", "warning");
  return false;
}


async function loadAdvancedSettings() {
  try {
    const data = await apiRequest<Partial<AdvancedSettingsState> & { settings?: Record<string, number | string> }>("/api/advanced-settings");
    advancedSettings.value = { ...advancedSettings.value, ...(data.settings || {}) };
    if (data.selection) executionSelection.value = data.selection;
    mergeManualRanges(data.manual_ranges);
  } catch (error) {
    deps.notify(errorMessage(error, "高级设置加载失败"), "warning");
  }
}


async function saveAdvancedSettings() {
  advancedBusy.value = true;
  try {
    const data = await settingsApi.saveCustom(currentExecutionSettings());
    advancedSettings.value = { ...advancedSettings.value, ...(data.settings || {}) };
    executionSelection.value = "custom";
    deps.notify("已保存为自定义档，当前档位已切换", "success");
  } catch (error) {
    deps.notify(errorMessage(error, "高级设置保存失败"), "error");
  } finally {
    advancedBusy.value = false;
  }
}


function currentExecutionSettings(): ExecutionSettings {
  return Object.fromEntries(SPEED_FIELDS.map((field) => [field, Number(advancedSettings.value[field])])) as unknown as ExecutionSettings;
}


// 范围预览复用：记录生成 preview 时的范围参数摘要，切档位等场景参数未变则直接复用，
// 避免重复请求 /api/search-scope/preview。
let scopePreviewKey = "";
function currentScopePreviewKey(): string {
  return JSON.stringify([
    draftPlatform.value,
    [...selectedKeywords.value].sort(),
    [...cityList.value].sort(),
    locationDraft.allLocations(draftPlatform.value, cityList.value),
    pagesValue.value,
  ]);
}


async function refreshScopePreview(): Promise<FrozenSearchScope | null> {
  // Spec041 返工：所有出口都推进请求序号——新平台即使不发新请求（例如没选
  // 关键词），旧平台的在飞响应也必须失效，不能覆盖新平台。
  const reqId = ++scopePreviewReqId.value;
  const requestPlatform = draftPlatform.value;
  const isStale = () => reqId !== scopePreviewReqId.value || requestPlatform !== draftPlatform.value;
  if (!selectedKeywords.value.length) {
    setScopePreviewFor(requestPlatform, null);
    // Spec041 补丁：本次调用已推进请求序号，在飞的旧请求不会再来清"忙"，
    // 这里不清就会永久卡在 busy（schema 不可用分支同样要清）。
    scopePreviewBusy.value = false;
    return null;
  }
  // 平台切换期间 schema 仍属于旧平台，或当前平台明确禁用新任务时，
  // 不向后端提交范围预览；否则会把一个预期的“平台不可用”状态制造成
  // 503 警告。schema 就绪后由 watcher 重新尝试可用平台的预览。
  if (schemaBusy.value || draftPlatformDisabled.value) {
    setScopePreviewFor(requestPlatform, null);
    scopePreviewBusy.value = false;
    return null;
  }
  scopePreviewBusy.value = true;
  try {
    const data = await settingsApi.previewScope({
      platform: requestPlatform,
      keywords: [...selectedKeywords.value],
      scope_kind: cityList.value.length ? "cities" : "nationwide",
      cities: cityList.value.length ? [...cityList.value] : [],
      locations: locationDraft.allLocations(requestPlatform, cityList.value),
      pages_per_combination: pagesValue.value,
    });
    if (isStale()) return scopePreview.value;
    const preview = normalizeScopePreview(data);
    // 只写到发起请求的那个平台槽位：切平台后旧响应不会污染新平台。
    setScopePreviewFor(requestPlatform, preview);
    scopePreviewKey = currentScopePreviewKey();
    return preview;
  } catch (error) {
    if (isStale()) return scopePreview.value;
    setScopePreviewFor(requestPlatform, null);
    deps.notify(errorMessage(error, "搜索范围校验失败"), "warning");
    return null;
  } finally {
    if (reqId === scopePreviewReqId.value) scopePreviewBusy.value = false;
  }
}


async function selectExecutionMode(selection: ExecutionSelection) {
  // 切换档位不改变搜索范围：范围参数未变时直接复用已有 preview，只发一次 select-mode 请求，
  // 避免每次切档都重复请求 /api/search-scope/preview（消除切档卡顿）。
  const preview = scopePreview.value && scopePreviewKey === currentScopePreviewKey()
    ? scopePreview.value
    : await refreshScopePreview();
  if (!preview) return;
  advancedBusy.value = true;
  try {
    const data = await settingsApi.selectMode(selection, preview.scope_digest);
    const returned = (data as unknown as { settings?: ExecutionSettings; config?: ExecutionSettings }).settings
      || (data as unknown as { config?: ExecutionSettings }).config;
    if (!returned) throw new Error("模式响应缺少完整执行配置");
    advancedSettings.value = { ...advancedSettings.value, ...returned };
    executionSelection.value = selection;
  } catch (error) {
    deps.notify(errorMessage(error, "执行模式切换失败"), "error");
  } finally {
    advancedBusy.value = false;
  }
}


function mergeManualRanges(raw: AdvancedSettingsState["manual_ranges"] | undefined) {
  if (!raw) return;
  for (const [field, value] of Object.entries(raw)) {
    const range = Array.isArray(value) ? value : [value.min, value.max];
    if (range.length === 2 && range.every((item) => Number.isFinite(item)) && range[0] <= range[1]) {
      advancedRanges.value[field] = [Number(range[0]), Number(range[1])];
    }
  }
}


function advancedRange(field: string): [number, number] {
  return advancedRanges.value[field] || [0, Number.MAX_SAFE_INTEGER];
}


function clampAdvanced(field: string) {
  const raw = advancedSettings.value[field];
  if (typeof raw !== "number" || Number.isNaN(raw)) return;
  const range = advancedRanges.value[field];
  if (!range) return;
  const [min, max] = range;
  let next = raw;
  if (next < min) next = min;
  else if (next > max) next = max;
  if (next !== raw) advancedSettings.value[field] = next;
}

return {
  showLoginGuide,
  isLoginErrorCode,
  confirmNationalScope,
  cancelNationalScope,
  setDraftPlatform,
  requestDraftPlatform,
  cancelPlatformSwitch,
  confirmPlatformSwitch,
  loadFilterLabels,
  loadCityCatalog,
  confirmCities,
  addCustomCity,
  removeCity,
  toggleFilter,
  chooseFile,
  handleDrop,
  analyzeResume,
  resumeAnalysisFlow: resumeFlow,
  initializeFromAnalysis,
  applyResumeAnalysisToCurrentSchema,
  toggleKeyword,
  removeKeyword,
  addCustomKeyword,
  confirmProfile,
  handleProfileInput,
  handleProfileBlur,
  validateProfileForScreen,
  requireProfileConfirmed,
  loadAdvancedSettings,
  saveAdvancedSettings,
  currentExecutionSettings,
  refreshScopePreview,
  selectExecutionMode,
  mergeManualRanges,
  advancedRange,
  clampAdvanced,
};
}

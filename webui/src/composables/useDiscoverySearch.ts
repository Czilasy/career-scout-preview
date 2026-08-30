// 021 B8 T027：DiscoveryView search 动作层（自 DiscoveryView.vue script 原样搬运，函数体零改动，跨域引用经 deps 调用时解析）。
// 031 B8：deps 形参类型 = discoveryDeps.ts 的 SearchNeeds（跨域依赖契约）。
import type { Ref } from "vue";
import type { DiscoveryState } from "./useDiscoveryState";
import type { SearchNeeds } from "./discoveryDeps";
import { nextTick } from "vue";
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
  partitionPipelineResult,
  projectResumeSuggestionToSchema,
  shouldConfirmNationalScope,
  singleSelectNextValue,
} from "../discovery";
import { setThemePlatform } from "../composables/useTheme";
import type { AnalyzeResponse } from "./useDiscoveryState";

export function useDiscoverySearch(state: DiscoveryState, deps: SearchNeeds) {
  const { LOGIN_ERROR_CODES, SPEED_FIELDS, activeCategory, advancedBusy, advancedRanges, advancedSettings, aiConsent, analysisReady, appliedResumePlatforms, autoScreenArmed, cityCatalogBusy, cityCatalogRef, cityList, cityLoader, cityText, currentRoundStatus, customCity, customKeyword, draftPlatform, dragActive, executionSelection, fieldLabels, filterGroups, filterValues, finishedPartial, historyBackToLatest, historyRound, interruptedRunId, keywords, locationDraft, loginGuide, nationalScopeConfirm, oneClickOpen, pagesValue, pausedRunId, pendingPlatformSwitch, pipelineResult, pipelineResultRunId, platformState, profileConfirmed, profileError, profileFacts, profileInputEl, profileSummary, recrawlPlatformGuide, recrawlSnapshot, recrawlTaskId, rejectedIds, restoredTaskHint, resultLoaded, resultPlatformFilter, resultRunIds, resumeAnalysis, resumeError, schemaBusy, schemaLoader, schemaRef, scopePreview, scopePreviewBusy, scopePreviewReqId, scrapeCompleted, scrapeSnapshot, scrapeTaskId, screenBusy, screenSnapshot, screenTaskId, selectedFile, selectedKeywords, uploadBusy } = state;
  const { cancelActiveTasksForNewRound, clearLatestResult, enterSearchStep, notify, openOneClickDialog, restoreRunningTask, startScrape } = deps;


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
  // B007：切平台视为新草稿，清掉旧 run 身份与 scope 快照；双平台结果保留。
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
async function loadFilterLabels(platform: Platform = platformState.draft) {
  if (schemaLoader.loadedPlatform === platform && schemaRef.value) return;
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
    if (schemaLoader.pendingPlatform === null) schemaBusy.value = false;
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


async function analyzeResume() {
  resumeError.value = "";
  if (!selectedFile.value) {
    deps.notify("请先选择简历文件", "warning");
    return;
  }
  if (!aiConsent.value) {
    deps.notify("请勾选 AI 解析同意后再继续", "warning");
    return;
  }
  uploadBusy.value = true;
  try {
    if (!(await deps.cancelActiveTasksForNewRound())) return;
    if (!(await deps.clearLatestResult())) return;
    const form = new FormData();
    form.append("file", selectedFile.value);
    form.append("platform", draftPlatform.value);
    resumeAnalysis.value = null;
    appliedResumePlatforms.value = new Set();
    const data = await apiRequest<AnalyzeResponse>("/api/analyze-resume", {
      method: "POST",
      body: form,
    });
    historyRound.value = null;
    historyBackToLatest();
    scrapeTaskId.value = "";
    screenTaskId.value = "";
    recrawlTaskId.value = "";
    scrapeSnapshot.value = null;
    screenSnapshot.value = null;
    recrawlSnapshot.value = null;
    pipelineResultRunId.value = "";
    resultPlatformFilter.value = "all";
    finishedPartial.value = false;
    recrawlPlatformGuide.value = null;
    resultRunIds.value = { boss: "", zhilian: "" };
    pausedRunId.value = "";
    interruptedRunId.value = "";
    restoredTaskHint.value = "";
    scopePreview.value = null;
    autoScreenArmed.value = false;
    locationDraft.reset();
    oneClickOpen.value = false;
    scopePreviewBusy.value = false;
    currentRoundStatus.value = "";
    activeCategory.value = "matched";
    initializeFromAnalysis(data);
    analysisReady.value = true;
    scrapeCompleted.value = false;
    resultLoaded.value = false;
    pipelineResult.value = null;
    rejectedIds.value = new Set();
    deps.enterSearchStep();
    deps.notify("简历分析完成，请确认关键词与城市", "success");
  } catch (error) {
    resumeError.value = "失败，点击重试";
    deps.notify(errorMessage(error, "简历分析失败"), "error");
  } finally {
    uploadBusy.value = false;
  }
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
  const projected = semantic
    ? projectResumeSuggestionToSchema(semantic, schema)
    : {};
  if (!semantic) {
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
  if (!selectedKeywords.value.length) {
    scopePreview.value = null;
    return null;
  }
  const reqId = ++scopePreviewReqId.value;
  scopePreviewBusy.value = true;
  try {
    const data = await settingsApi.previewScope({
      platform: draftPlatform.value,
      keywords: selectedKeywords.value,
      scope_kind: cityList.value.length ? "cities" : "nationwide",
      cities: cityList.value.length ? cityList.value : [],
      locations: locationDraft.allLocations(draftPlatform.value, cityList.value),
      pages_per_combination: pagesValue.value,
    });
    if (reqId !== scopePreviewReqId.value) return scopePreview.value;
    scopePreview.value = normalizeScopePreview(data);
    scopePreviewKey = currentScopePreviewKey();
    return scopePreview.value;
  } catch (error) {
    if (reqId !== scopePreviewReqId.value) return scopePreview.value;
    scopePreview.value = null;
    deps.notify(errorMessage(error, "搜索范围校验失败"), "warning");
    return null;
  } finally {
    scopePreviewBusy.value = false;
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
import type {
  FrozenSearchScope,
  JobItem,
  Platform,
  PlatformCityCatalog,
  PlatformFilterSchema,
  LocationCondition,
  ScopePreviewResponse,
  IntegritySnapshot,
} from "./types";
import { buildLocationPayload } from "./location";

export interface PipelineResult {
  ok?: boolean;
  /** 运行时结果身份；后端快照以外层 platform 返回，前端合并时回填。 */
  platform?: Platform;
  jobs?: JobItem[];
  dropped?: JobItem[];
  total_scraped?: number;
  total_kept?: number;
  total_matched?: number;
  total_dropped?: number;
  profile_summary?: string;
  error?: string;
  integrity?: IntegritySnapshot | null;
}

export interface PipelineGroups {
  matched: JobItem[];
  unmatched: JobItem[];
  uncertain: JobItem[];
  dropped: JobItem[];
}

export type ResultPlatformFilter = "all" | "boss" | "zhilian";

// 037 复审：新增 "jd"（JD 详情抓取）。旧枚举只有 scraping/screening，
// 后端 stage=fetch_jd（抓 JD）与 screen_b（AI 精筛）都落进 screening，
// 灵动岛无法区分（用户实测：抓 JD 时显示"AI精筛"）。
export type RoundStatusPhase = "scraping" | "jd" | "screening" | "judged" | "scraped";
export type RoundStatusScope = "all" | "boss" | "zhilian" | "history";
export interface RoundStatusPayload {
  platform: Platform;
  phase: RoundStatusPhase;
  judged: number;
  scope: RoundStatusScope;
}

/**
 * 平台显示名唯一权威（全项目只有这一处登记平台名）。
 * 新增平台只在这里加一行；未登记的身份给中性中文，
 * 绝不默认成某个平台，也不把内部码原样吐到界面上。
 */
const PLATFORM_LABELS: Record<string, string> = {
  boss: "BOSS",
  zhilian: "智联",
};
const UNKNOWN_PLATFORM_LABEL = "其它平台";

export function platformLabel(platform?: string | null): string {
  const key = String(platform ?? "").trim().toLowerCase();
  if (!key) return "";
  return PLATFORM_LABELS[key] || UNKNOWN_PLATFORM_LABEL;
}

export function roundScopeLabel(scope: RoundStatusScope, platform: Platform): string {
  if (scope === "all") return "全部";
  if (scope === "history") return "历史轮次";
  return platformLabel(platform);
}

// ---------------------------------------------------------------------------
// 阶段卡状态口径（树干唯一一份）：Flow 呈现层、轨道头部与卡体都从这里取，
// 不在各自文件里抄清单或抄词表——两份清单会各自漂移（历史上就漏过 pausing），
// 两份词表会让同一张卡的头部与卡体说两句不同结论。
// ---------------------------------------------------------------------------

/** 轨道级活动态：整条线还活着（排队 / 进行中）。 */
export const ACTIVE_TRACK_STATUSES = ["queued", "running"];
/** 与 Flow API 终态门禁一致：结果可读，轨道不能再次操作。 */
export const TERMINAL_TRACK_STATUSES = ["done", "succeeded", "failed", "stopped", "cancelled"];

/**
 * 轨道问题态（树干唯一一份）：整条线已经停下、需要用户处理，界面据此说「失败 / 不可用 /
 * 已中断」。不含用户主动暂停——暂停是用户自己按下的出口，还有「就地继续」可走，把它算成
 * 问题会把一次暂停报成失败（状态所有权：清单只许这一处，消费者一律引用）。
 */
export const TRACK_PROBLEM_STATUSES = ["failed", "interrupted", "unavailable"];

/**
 * 轨道不再活动的全集：问题态 + 用户主动暂停，只由上面那份派生，不再另抄一遍。
 * 阶段卡用它判「这条线已经不跑了，本段按自己的口径定格」。
 */
export const TRACK_STOPPED_STATUSES = [...TRACK_PROBLEM_STATUSES, "paused"];

/**
 * 本段快照还没说完自己那段话的状态（空串＝后端还没写状态，同样没说完）。
 * 轨道已经不是活动态时，这些状态要按整条线定格；本段快照是这些状态时，
 * 它继续替整条线说话。
 */
export const STAGE_IN_FLIGHT_STATUSES = ["", "queued", "pending", "running", "pausing"];

/**
 * 在飞清单里「还没有 worker 在跑」的状态：空串＝后端还没写状态，
 * queued／pending＝尚未开始。它们和已中断／已暂停／终态一样，时长由后端定格。
 */
const STAGE_NO_LIVE_WORKER_STATUSES = ["", "queued", "pending"];

/**
 * 计时是否走活表（可否在定格值上叠加本地每秒增量）的唯一判定，消费方只许调这一个函数。
 * 在飞清单说这段还没说完自己的话，其中只有真的有活 worker 在跑（进行中／正在暂停）
 * 才允许前端把表继续往下走；已中断／已暂停／尚未开始／终态一律只报后端定格值——
 * 否则后端把时长定住了，前端又用本地时钟把它演成还在跑。
 */
export function elapsedRunsLive(status?: string | null): boolean {
  const explicit = String(status || "");
  return STAGE_IN_FLIGHT_STATUSES.includes(explicit) && !STAGE_NO_LIVE_WORKER_STATUSES.includes(explicit);
}

/**
 * 本段已经有 run 身份（真的跑过）、这一轮却读不到任何状态时的兜底口径。
 * 与「从没开始的段」（中性 queued／等待开始）必须分开：跑过的段不能说成没跑过。
 */
export const UNREADABLE_STAGE_STATUS = "unknown";

/**
 * 完成族：done / succeeded 是后端轨道白名单终态，completed / completed_with_pending /
 * partial 是任务状态接口对同一事实的公开别名（webui/task_status.py）。
 */
export const STAGE_COMPLETED_STATUSES = [
  "done", "succeeded", "completed", "completed_with_pending", "partial",
];

/** 白箱完整性结论的说法（卡体独说的口径，头部必须说同一句）。 */
const STAGE_INTEGRITY_LABELS: Record<string, string> = {
  succeeded: "完整成功",
  empty: "已完成，没有找到岗位",
  partial: "部分完成，部分结果可能缺失",
  failed: "执行失败",
  unverifiable: "无法确认是否完成",
  interrupted: "任务已中断",
};

/** 生命周期状态比白箱结论更有话要说：失败/中断/停止/暂停不写成「完整成功」。 */
const STAGE_STATUS_OVERRIDES_INTEGRITY = [
  "failed", "interrupted", "cancelled", "stopped", "unavailable", "paused",
];

/**
 * 阶段状态词的唯一计算：轨道头部徽章与卡体（TaskProgress）都调这一个函数，
 * 同一张卡不会出现两句不同结论——后端把白箱 unverifiable 公开成
 * completed_with_pending 时，头部也不许把「无法确认是否完成」报成「完成，但有待确认」。
 */
export function stageStatusLabel(status?: string | null, integrityConclusion?: string | null): string {
  const explicit = String(status || "");
  const conclusion = String(integrityConclusion || "");
  if (conclusion && !STAGE_STATUS_OVERRIDES_INTEGRITY.includes(explicit)) {
    return STAGE_INTEGRITY_LABELS[conclusion] || "运行中";
  }
  if (explicit === "completed_with_pending" || explicit === "partial") return "完成，但有待确认";
  if (STAGE_COMPLETED_STATUSES.includes(explicit)) return "已完成";
  if (explicit === "failed") return "执行失败";
  if (explicit === "unavailable") return "暂不可用";
  if (explicit === "cancelled" || explicit === "stopped") return "已停止";
  if (explicit === "paused") return "已暂停";
  if (explicit === "pausing") return "正在暂停";
  if (explicit === "interrupted") return "已中断";
  if (explicit === "queued") return "等待开始";
  // 跑过但读不到状态的段：只说读不到，不说没开始、也不演成在跑。
  if (explicit === UNREADABLE_STAGE_STATUS) return "状态更新中";
  return "运行中";
}

export function historyStatusLabel(status: string, jobCount: number): string {
  const normalized = String(status || "").toLowerCase();
  if (["scraped_only"].includes(normalized)) return "已抓取，未筛选";
  if (["done", "succeeded", "completed"].includes(normalized)) return "完成";
  if (["partial", "completed_with_pending"].includes(normalized)) return "部分结果";
  // 017-US3: 标签只有三种；存量已清，未知状态不渲染（防御性空白优于错误文案）
  void jobCount;
  return "";
}
/** 纯展示层平台过滤：按 job.platform 过滤 jobs/dropped；"all" 原样返回。
 *
 * 依赖后端保证每个岗位带 platform 身份（实时任务结果按任务平台回填、
 * DB 恢复路径按 screening_results.platform 读取）；缺 platform 的岗位
 * 在任何单一平台视图下都会被过滤掉，因此过滤前调用方应回填平台身份。
 */
export function filterPipelineResultByPlatform(
  result: PipelineResult,
  filter: ResultPlatformFilter,
): PipelineResult {
  if (filter === "all") return result;
  return {
    ...result,
    jobs: (Array.isArray(result.jobs) ? result.jobs : []).filter((job) => job.platform === filter),
    dropped: (Array.isArray(result.dropped) ? result.dropped : []).filter((job) => job.platform === filter),
  };
}

function uniqueNonEmpty(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}

export function buildSearchScriptParams(keywords: string[], cities: string[], locations: LocationCondition[] = []) {
  return {
    keyword: uniqueNonEmpty(keywords).join(","),
    city: uniqueNonEmpty(cities),
    ...(locations.length ? { locations: buildLocationPayload(locations) } : {}),
    filters: {},
  };
}

/** B042：关键词有、城市空时两个开始入口需要确认后按全国继续。 */
export function shouldConfirmNationalScope(
  keywords: string[],
  cities: string[],
): boolean {
  const hasKeyword = keywords.some((item) => item.trim());
  const hasCity = cities.some((item) => item.trim());
  return hasKeyword && !hasCity;
}

/** 「全国」不是城市：不选城市就是全国范围，它不该作为城市名留在草稿里。 */
export function isNationwideCityName(name: string): boolean {
  return name.trim() === "全国";
}

/** 过滤掉「全国」哨兵，只保留真实城市。 */
export function stripNationwideCities(cities: string[]): string[] {
  return cities.filter((city) => !isNationwideCityName(city));
}

export function partitionPipelineResult(result: PipelineResult): PipelineGroups {
  const groups: PipelineGroups = {
    matched: [],
    unmatched: [],
    uncertain: [],
    dropped: Array.isArray(result.dropped) ? result.dropped : [],
  };
  for (const job of Array.isArray(result.jobs) ? result.jobs : []) {
    if (job.verdict === "match") groups.matched.push(job);
    else if (job.verdict === "not_match") groups.unmatched.push(job);
    else groups.uncertain.push(job);
  }
  return groups;
}

export function normalizeScopePreview(response: ScopePreviewResponse): FrozenSearchScope {
  if (!response.ok || !response.scope?.scope_digest) {
    throw new TypeError("backend scope preview is incomplete");
  }
  return {
    ...response.scope,
    keywords: [...response.scope.keywords],
    cities: [...response.scope.cities],
  };
}

export function projectResumeSuggestionToSchema(
  semantic: Record<string, string[]>,
  schema: PlatformFilterSchema,
): Record<string, string[]> {
  const projected: Record<string, string[]> = {};
  for (const field of schema.fields) {
    const labels = semantic[field.key] || [];
    const selected: string[] = [];
    for (const label of labels) {
      const option = field.options.find((opt) => opt.label === label);
      if (option) selected.push(option.value);
    }
    if (selected.length) projected[field.key] = selected;
  }
  return projected;
}

// ---------------------------------------------------------------------------
// T502：平台身份状态容器（contracts/platform-schema.md L142-159 + tasks006 L27）
// ---------------------------------------------------------------------------
// 三身份独立：草稿平台 / 任务平台 / 最近结果平台。
// 不引入 Vue reactivity，保持 discovery.ts 纯函数 + 闭包风格；T503 起由组件接到 ref。
// mock 阶段：DEFAULT_PLATFORM 硬编码 "boss"；T515 真实联调由组件从 /api/platforms.default_platform
// 读取后传入 createPlatformState(initial)。task / result 初始为 null（无运行任务、无最近结果）。

/** mock 阶段的默认草稿平台。真实联调阶段由组件从后端读取后传入工厂。 */
export const DEFAULT_PLATFORM: Platform = "boss";

/**
 * 三身份独立平台状态容器。
 * 不变式（platform-schema.md L142-159）：
 *  1. setDraftPlatform 不改 task/result
 *  2. setTaskPlatform 不改 draft/result（任务恢复）
 *  3. setResultPlatform 不改 draft/task；不触发草稿切换
 *  4. 不用 watcher 相互覆盖
 */
export interface PlatformState {
  readonly draft: Platform;
  readonly task: Platform | null;
  readonly result: Platform | null;
  /** 草稿切换：只作用于新任务草稿，不改 task/result。 */
  setDraftPlatform: (platform: Platform) => void;
  /** 任务恢复：从任务响应设置任务平台，不改 draft/result；null 表示无运行任务。 */
  setTaskPlatform: (platform: Platform | null) => void;
  /** 结果加载：从结果 snapshot 设置结果平台，不改 draft/task；不触发草稿切换。null 表示无最近结果。 */
  setResultPlatform: (platform: Platform | null) => void;
}

export function createPlatformState(initial: Platform = DEFAULT_PLATFORM): PlatformState {
  let draft = initial;
  let task: Platform | null = null;
  let result: Platform | null = null;
  return {
    get draft() {
      return draft;
    },
    get task() {
      return task;
    },
    get result() {
      return result;
    },
    setDraftPlatform(platform: Platform) {
      draft = platform;
    },
    setTaskPlatform(platform: Platform | null) {
      task = platform;
    },
    setResultPlatform(platform: Platform | null) {
      result = platform;
    },
  };
}

// ---------------------------------------------------------------------------
// T504/T505：异步平台资源加载器（contracts/platform-schema.md L151-156）
// ---------------------------------------------------------------------------
// platform-schema.md L151-156 要求：发出请求时捕获目标平台和请求版本；
// 响应返回后同时校验请求仍为该资源的最新请求、响应平台与目标平台一致；
// 任一校验失败时丢弃响应，不更新 schema、城市、筛选草稿、加载状态或错误状态；
// 快速切换导致旧请求被取消不显示为当前平台错误。
//
// 用于 /api/filter-labels?platform=（PlatformFilterSchema）和
// /api/options?platform=（PlatformCityCatalog）。两者结构都带 platform 字段，
// 故用泛型 T extends { platform: Platform } 复用同一份序号 + 取消 + 校验逻辑。

export interface AsyncResourceLoader<T extends { platform: Platform }> {
  /** 当前已加载平台（仅成功响应后才更新；旧响应被丢弃时不更新）。 */
  readonly loadedPlatform: Platform | null;
  /** 当前已加载数据；仅当 loadedPlatform !== null 时有效。 */
  readonly data: T | null;
  /** 当前正在请求的平台（发出请求即设；请求结束清空）。 */
  readonly pendingPlatform: Platform | null;
  /** 上次错误（仅当最新请求失败时写入；旧请求被取消或被覆盖不写错误）。 */
  readonly error: string | null;
  /**
   * 发起请求；若已有请求在跑会取消旧的。
   * 返回 true 当且仅当响应被采纳（仍是最新请求 + 响应平台匹配）。
   * fetcher 应尊重 signal.aborted 并抛 AbortError 以释放资源；
   * 但即使 fetcher 忽略 signal，load 内部仍会通过 reqId 校验丢弃旧响应。
   */
  load(
    platform: Platform,
    fetcher: (platform: Platform, signal: AbortSignal) => Promise<T>,
  ): Promise<boolean>;
  /** 取消任何在途请求；后续 load 仍可正常发起。 */
  cancel(): void;
}

export function createAsyncResourceLoader<T extends { platform: Platform }>(): AsyncResourceLoader<T> {
  let loadedPlatform: Platform | null = null;
  let data: T | null = null;
  let pendingPlatform: Platform | null = null;
  let error: string | null = null;
  let reqId = 0;
  let activeController: AbortController | null = null;
  return {
    get loadedPlatform() {
      return loadedPlatform;
    },
    get data() {
      return data;
    },
    get pendingPlatform() {
      return pendingPlatform;
    },
    get error() {
      return error;
    },
    async load(platform, fetcher) {
      // 取消旧请求（不变式：旧请求的 signal 被 abort，旧 fetcher 应据此释放）
      if (activeController) activeController.abort();
      reqId += 1;
      const myReqId = reqId;
      pendingPlatform = platform;
      const myController = new AbortController();
      activeController = myController;
      try {
        const result = await fetcher(platform, myController.signal);
        // 校验 1：仍是最新请求（被后续 load 覆盖则丢弃）
        if (myReqId !== reqId) return false;
        // 校验 2：响应平台匹配目标平台（防后端串台）
        if (result.platform !== platform) return false;
        loadedPlatform = platform;
        data = result;
        error = null;
        pendingPlatform = null;
        return true;
      } catch (err) {
        // 旧请求的错误不污染 error 状态（platform-schema.md L156）
        if (myReqId !== reqId) return false;
        error = err instanceof Error ? err.message : String(err);
        pendingPlatform = null;
        return false;
      }
    },
    cancel() {
      if (activeController) activeController.abort();
      reqId += 1; // 让在途请求的 myReqId 不再匹配，使其响应被丢弃
      pendingPlatform = null;
    },
  };
}

/** 便于 DiscoveryView 区分 schema / 城市两类资源加载器实例的别名。 */
export type SchemaLoader = AsyncResourceLoader<PlatformFilterSchema>;
export type CityCatalogLoader = AsyncResourceLoader<PlatformCityCatalog>;
export function createSchemaLoader(): SchemaLoader {
  return createAsyncResourceLoader<PlatformFilterSchema>();
}
export function createCityCatalogLoader(): CityCatalogLoader {
  return createAsyncResourceLoader<PlatformCityCatalog>();
}

/**
 * 028：单选筛选字段（第 7 类「招聘者上次活跃」）点选后的新取值。
 * 点已选值 = 取消（清空）、点新值 = 替换；多选字段（multiple 非 false）
 * 返回 null，调用方走原有多选增删逻辑。
 */
export function singleSelectNextValue(
  multiple: boolean | undefined,
  current: string[],
  code: string,
): string[] | null {
  if (multiple !== false) return null;
  return current.includes(code) ? [] : [code];
}

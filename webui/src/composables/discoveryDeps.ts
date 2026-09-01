// 031 B8：Discovery 跨域依赖契约（contracts/discovery-deps.md / data-model E6）。
//
// 背景：5 个 Discovery composable 原以无类型的 deps 参数接收共享容器，跨域调用
// 全靠 DiscoveryView 构造后逐行回填（`shared.X = ...`），编译器看不见调用图，
// 任何成员漏填都只能在运行时炸。
//
// 本文件把跨域依赖面固定成类型，只搬形状、不动调用时序（行为冻结）：
//   - 五域「提供接口」（WorkflowDeps / SearchDeps / ExecutionDeps / TasksDeps /
//     ResultsDeps）＝该域 composable 对外暴露、供他域取用的成员；
//   - 五域「消费接口」（*Needs）＝该 composable 实际从 deps 取用的最小成员集，
//     每个 composable 只声明自己用到的成员（接口隔离）；
//   - `createDiscoveryDeps` 建容器 → `wireDiscoveryDeps` 一次成型接线 →
//     `attachRoundFlow` 补 roundFlow，缺成员即编译错误。
//
// 顺序耦合说明（无法消除，只做封装）：五域互相依赖（search 用 execution 的
// startScrape，execution 用 search 的 requireProfileConfirmed），roundFlow 的
// api 又依赖五域产物，因此容器必须先建、后接线。composable 只在**调用时**读取
// deps 成员，接线完成前不会触碰，语义与迁出前逐行回填完全一致。

import type {
  FrozenSearchScope,
  JobItem,
  Notice,
  Platform,
  RoundContext,
} from "../types";
import type { PipelineResult } from "../discovery";
import type {
  AiScreenLaunch,
  DiscoveryEmit,
  DiscoveryProps,
  MergedLatestResult,
  OneClickLaunch,
  TaskSnapshot,
} from "./useDiscoveryState";

// emit/props 形状定义在 useDiscoveryState（它是二者的直接消费方），此处转出，
// 保持 discoveryDeps 单一来源：视图与各 composable 都从这里取类型。
export type { DiscoveryEmit, DiscoveryProps };

// 035：liveTaskStep 为 useDiscoveryState 只读派生（未结束任务的真实进度页：
// 抓取活→02，筛选/重抓活→03，无活任务→空），经契约转出供跨域消费——
// useScreenRoundFlow（refs 判定面 deriveLiveTaskStep）、useDiscoverySearch /
// useDiscoveryResults（liveTaskStep(state)）的入口守卫与回最新落点统一取用。
export { deriveLiveTaskStep, liveTaskStep } from "./useDiscoveryState";
export type { LiveTaskProbe } from "./useDiscoveryState";

/** roundFlow 被跨域消费的最小面（useScreenRoundFlow 经 reactive 包装后的形状）。 */
export interface RoundFlowLike {
  busyAction: string;
  roundContext: RoundContext | null;
  roundContexts: Partial<Record<Platform, RoundContext | null>>;
  // 保存结果后恢复画像时置位，避免画像 watch 把 profileConfirmed 打回 false。
  suppressProfileWatch: boolean;
  startRecrawl: (platform?: Platform) => Promise<void>;
  clearRoundContext: () => void;
  restoreRoundContext: (payload: Partial<RoundContext> | null | undefined) => boolean;
  registerRoundContext: (
    platform: Platform,
    payload: Partial<RoundContext> | null | undefined,
  ) => void;
}

// ---------------------------------------------------------------------------
// 五域提供接口：成员 = 该域 composable 对外暴露、回填进共享容器的成员
// ---------------------------------------------------------------------------

export interface WorkflowDeps {
  notify: (message: string, tone?: Notice["tone"]) => void;
  enterSearchStep: () => void;
  enterScreenStep: () => void;
  clearWorkflowState: () => void;
  markResultsPageSeen: () => void;
  persistFinishedState: () => void;
  clearFinishedState: () => void;
}

export interface SearchDeps {
  setDraftPlatform: (platform: Platform) => void;
  loadCityCatalog: (platform?: Platform) => Promise<void>;
  loadFilterLabels: (platform?: Platform) => Promise<void>;
  refreshScopePreview: () => Promise<FrozenSearchScope | null>;
  requireProfileConfirmed: () => boolean;
  validateProfileForScreen: () => boolean;
  showLoginGuide: (platform: Platform) => Promise<void>;
  isLoginErrorCode: (code: unknown) => boolean;
}

export interface ExecutionDeps {
  startScrape: (options?: OneClickLaunch) => Promise<void>;
  openOneClickDialog: () => void;
  restoreRunningTask: () => Promise<void>;
  finishPausedTask: (runId: string) => Promise<void>;
  continueAiScreen: (platform?: Platform) => Promise<void>;
  cancelScrape: () => Promise<void>;
  startAiScreen: (options?: AiScreenLaunch) => Promise<void>;
}

export interface TasksDeps {
  cancelActiveTasksForNewRound: () => Promise<boolean>;
  mergeRecrawlUpdates: (updates: Record<string, unknown>) => void;
  pollRecrawl: (taskId: string) => Promise<void>;
  pollTask: (taskId: string, kind: "scrape" | "screen") => Promise<void>;
  saveScrapedOnlySnapshot: (
    markViewed?: boolean,
  ) => Promise<"saved" | "zero" | "failed">;
  enrichPausedSnapshot: (
    runId: string,
    snapshot: TaskSnapshot,
    kind: "scrape" | "screen" | "recrawl",
  ) => Promise<void>;
  isCompletedTaskStatus: (status?: string) => boolean;
}

export interface ResultsDeps {
  // 返回 false 表示"有活动任务，未清"，调用方据此中止当前动作。
  clearLatestResult: () => Promise<boolean>;
  loadLatestResult: (opts?: { skipTerminalSnapshot?: boolean }) => Promise<void>;
  setPipelineResult: (result: PipelineResult) => void;
  fetchMergedLatestResult: () => Promise<MergedLatestResult | null>;
  returnToLatest: () => Promise<void>;
  restoreLocationsFromContext: (ctx?: Partial<RoundContext> | null) => void;
  jobId: (job: JobItem) => string;
}

/** 容器核心：非五域产物，构造时即可确定。 */
export interface DiscoveryCoreDeps {
  emit: DiscoveryEmit;
  props: DiscoveryProps;
  roundFlow: RoundFlowLike;
}

/** 聚合：五域 + 核心，composable 的 deps 参数即从此类型 Pick。 */
export interface DiscoveryDeps
  extends DiscoveryCoreDeps,
    WorkflowDeps,
    SearchDeps,
    ExecutionDeps,
    TasksDeps,
    ResultsDeps {}

export interface DiscoveryPieces {
  workflow: WorkflowDeps;
  search: SearchDeps;
  execution: ExecutionDeps;
  tasks: TasksDeps;
  results: ResultsDeps;
}

// ---------------------------------------------------------------------------
// 五域消费接口：每个 composable 只声明自己从 deps 取用的最小成员集
// ---------------------------------------------------------------------------

export type WorkflowNeeds = Pick<DiscoveryDeps, "emit">;

export type SearchNeeds = Pick<
  DiscoveryDeps,
  | "cancelActiveTasksForNewRound"
  | "clearLatestResult"
  | "enterSearchStep"
  | "notify"
  | "openOneClickDialog"
  | "restoreRunningTask"
  | "startScrape"
>;

export type ExecutionNeeds = Pick<
  DiscoveryDeps,
  | "clearWorkflowState"
  | "enrichPausedSnapshot"
  | "enterScreenStep"
  | "enterSearchStep"
  | "isCompletedTaskStatus"
  | "isLoginErrorCode"
  | "loadCityCatalog"
  | "loadFilterLabels"
  | "loadLatestResult"
  | "notify"
  | "persistFinishedState"
  | "pollRecrawl"
  | "pollTask"
  | "refreshScopePreview"
  | "requireProfileConfirmed"
  | "restoreLocationsFromContext"
  | "returnToLatest"
  | "roundFlow"
  | "saveScrapedOnlySnapshot"
  | "setDraftPlatform"
  | "setPipelineResult"
  | "showLoginGuide"
  | "validateProfileForScreen"
>;

export type TasksNeeds = Pick<
  DiscoveryDeps,
  | "cancelScrape"
  | "clearFinishedState"
  | "clearLatestResult"
  | "clearWorkflowState"
  | "continueAiScreen"
  | "enterScreenStep"
  | "fetchMergedLatestResult"
  | "finishPausedTask"
  | "isLoginErrorCode"
  | "jobId"
  | "loadLatestResult"
  | "notify"
  | "restoreRunningTask"
  | "roundFlow"
  | "setPipelineResult"
  | "showLoginGuide"
  | "startAiScreen"
>;

export type ResultsNeeds = Pick<
  DiscoveryDeps,
  | "emit"
  | "notify"
  | "pollRecrawl"
  | "pollTask"
  | "props"
  | "roundFlow"
  | "setDraftPlatform"
>;

// ---------------------------------------------------------------------------
// 容器建/接线：把"先建后填"的顺序耦合收在这里，视图侧只调这三个函数
// ---------------------------------------------------------------------------

/**
 * 建立共享依赖容器（只有核心成员确定，五域成员稍后由 wireDiscoveryDeps 接线）。
 *
 * 跨域成员此刻不存在，故此处是全流程唯一的形状断言：五域产物与容器互相依赖，
 * 无法在构造时一次给全。断言后 `wireDiscoveryDeps` 的接线受完整类型检查。
 */
export function createDiscoveryDeps(
  core: Omit<DiscoveryCoreDeps, "roundFlow">,
): DiscoveryDeps {
  return { ...core } as DiscoveryDeps;
}

/** 一次性接线五域产物：缺任一成员即编译错误（契约 discovery-deps.md 约定 2）。 */
export function wireDiscoveryDeps(
  target: DiscoveryDeps,
  pieces: DiscoveryPieces,
): DiscoveryDeps {
  Object.assign(
    target,
    pieces.workflow,
    pieces.search,
    pieces.execution,
    pieces.tasks,
    pieces.results,
  );
  return target;
}

/**
 * 回填 roundFlow：它依赖五域产物（api 段传入 execution/tasks/results/workflow
 * 的函数），必须晚于五域构造；就地写入同一容器，composable 调用时解析。
 */
export function attachRoundFlow(
  target: DiscoveryDeps,
  roundFlow: RoundFlowLike,
): DiscoveryDeps {
  target.roundFlow = roundFlow;
  return target;
}

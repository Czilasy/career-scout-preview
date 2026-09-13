import { reactive, toRefs } from "vue";
import { apiRequest } from "../api";
import type { PipelineResult } from "../discovery";
import type { IntegritySnapshot, Platform } from "../types";

export interface HistoryRoundItem {
  run_id: string;
  platform: Platform;
  status: string;
  /** 035：该轮的抓取任务 id，供「查看该轮运行日志」按任务过滤。 */
  scrape_task_id?: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  total_scraped: number;
  total_kept: number;
  total_matched: number;
  mismatch_count: number;
  total_dropped: number;
  pending_count: number;
  keyword_summary: string;
  profile_summary_preview: string;
  archived_at?: string | null;
  is_latest: boolean;
  integrity?: IntegritySnapshot | null;
}

export interface HistoryRoundDetail {
  ok: boolean;
  has_result: boolean;
  source_run_id: string;
  platform: Platform;
  status: string;
  /** B038：未筛选轮补筛需要父抓取任务 id（来自 execution_params）。 */
  scrape_task_id?: string;
  saved_at?: string;
  started_at?: number | null;
  finished_at?: number | null;
  script_params?: Record<string, unknown>;
  execution_config?: Record<string, unknown>;
  source_summary?: Record<string, unknown>;
  source_outcomes?: Array<Record<string, unknown>>;
  integrity?: IntegritySnapshot | null;
  result: PipelineResult;
}

interface ResultHistoryState {
  open: boolean;
  items: HistoryRoundItem[];
  loading: boolean;
  error: string;
  detail: HistoryRoundDetail | null;
  detailLoading: boolean;
  deleting: boolean;
  deleteTarget: HistoryRoundItem | null;
}

const state = reactive<ResultHistoryState>({
  open: false,
  items: [],
  loading: false,
  error: "",
  detail: null,
  detailLoading: false,
  deleting: false,
  deleteTarget: null,
});

let loadSeq = 0;

/**
 * 历史浏览的用户意图序号：每次「点开另一轮」或「回到最新」都 +1。
 * 晚到的请求发现序号已变就自己作废，避免两条加载互相覆盖
 * （点另一轮先闪最新、点得急停在最新、点 BOSS 轮先闪智联）。
 */
let viewIntentSeq = 0;

/**
 * Spec041：历史列表/详情/删除/归档都必须限定在当前求职画像，
 * 且切画像时作废在飞请求、清空列表与详情，旧画像响应不得写回。
 */
let currentProfileId = "";

function profileQuery(): string {
  return currentProfileId
    ? `?profile_id=${encodeURIComponent(currentProfileId)}`
    : "";
}

/**
 * 绑定当前求职画像；画像变化时立即换槽（清列表/详情并作废在飞请求）。
 * 只做同步清场，不发请求：下次打开抽屉（show → loadHistory）时按新画像加载。
 */
export function setHistoryProfile(nextProfileId: string): void {
  const next = String(nextProfileId || "").trim();
  if (next === currentProfileId) return;
  currentProfileId = next;
  loadSeq += 1;
  viewIntentSeq += 1;
  state.items = [];
  state.detail = null;
  state.error = "";
  state.loading = false;
  state.detailLoading = false;
  state.deleteTarget = null;
}

export function currentHistoryIntent(): number {
  return viewIntentSeq;
}

export function formatHistoryTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const date = typeof value === "number"
    ? new Date(value)
    : new Date(String(value).replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function loadHistory(options: { silent?: boolean } = {}): Promise<void> {
  const seq = ++loadSeq;
  if (!options.silent) {
    state.loading = true;
    state.error = "";
  }
  try {
    const data = await apiRequest<{ ok: boolean; items?: HistoryRoundItem[] }>(
      `/api/result-history${profileQuery()}`,
    );
    if (seq !== loadSeq) return;
    state.items = data.items || [];
    state.error = "";
    if (!options.silent) {
      state.loading = false;
    }
  } catch (error) {
    if (seq !== loadSeq) return;
    if (!options.silent) {
      state.loading = false;
      state.error = error instanceof Error ? error.message : "历史列表读取失败";
    }
  }
}

function show() {
  state.open = true;
  void loadHistory();
}

function hide() {
  state.open = false;
}

async function openRound(runId: string): Promise<void> {
  const intent = ++viewIntentSeq;
  state.detailLoading = true;
  // 不在这里清空 detail：切换轮次不是「回到最新」，清空会被当成回最新
  // 而触发另一条异步加载，两条加载赛跑，谁后到谁定画面。
  try {
    const detail = await apiRequest<HistoryRoundDetail>(
      `/api/result-history/${encodeURIComponent(runId)}${profileQuery()}`,
    );
    if (intent !== viewIntentSeq) return;  // 期间又点了别的轮次：丢弃本次结果
    state.detail = detail;
    state.open = false;
  } catch (error) {
    if (intent !== viewIntentSeq) return;
    state.error = error instanceof Error ? error.message : "历史轮次读取失败";
    state.open = true;
  } finally {
    if (intent === viewIntentSeq) state.detailLoading = false;
  }
}

function backToLatest() {
  // 回到最新是明确的用户意图：作废仍在飞的轮次加载。
  viewIntentSeq += 1;
  state.detail = null;
}

function confirmDelete(item: HistoryRoundItem) {
  state.deleteTarget = item;
}

function cancelDelete() {
  state.deleteTarget = null;
}

async function deleteRound(item: HistoryRoundItem): Promise<void> {
  if (state.deleting) return;
  state.deleting = true;
  try {
    await apiRequest<{ ok: boolean }>(
      `/api/result-history/${encodeURIComponent(item.run_id)}${profileQuery()}`,
      { method: "DELETE" },
    );
    if (state.detail?.source_run_id === item.run_id) {
      backToLatest();
    }
    state.items = state.items.filter((round) => round.run_id !== item.run_id);
    state.deleteTarget = null;
    await loadHistory({ silent: true });
  } catch (error) {
    state.error = error instanceof Error ? error.message : "删除失败";
  } finally {
    state.deleting = false;
  }
}

/** 归档当前画像的当前结果（BOSS 与智联），保留为历史轮次。 */
async function archiveAllCurrentResults(): Promise<string[]> {
  const data = await apiRequest<{ ok: boolean; archived_run_ids?: string[] }>(
    "/api/result-history/archive-latest",
    {
      method: "POST",
      json: { profile_id: currentProfileId },
    },
  );
  await loadHistory();
  return data.archived_run_ids || [];
}

export function useResultHistory() {
  return {
    ...toRefs(state),
    loadHistory,
    show,
    hide,
    openRound,
    backToLatest,
    confirmDelete,
    cancelDelete,
    deleteRound,
    archiveAllCurrentResults,
    setProfile: setHistoryProfile,
  };
}

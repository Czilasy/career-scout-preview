import { reactive, toRefs } from "vue";
import { apiRequest } from "../api";
import type { PipelineResult } from "../discovery";
import type { Platform } from "../types";

export interface HistoryRoundItem {
  run_id: string;
  platform: Platform;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  total_scraped: number;
  total_kept: number;
  total_matched: number;
  total_dropped: number;
  pending_count: number;
  keyword_summary: string;
  profile_summary_preview: string;
  archived_at?: string | null;
  is_latest: boolean;
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

export function formatHistoryTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const date = typeof value === "number"
    ? new Date(value)
    : new Date(String(value).replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function loadHistory(): Promise<void> {
  const seq = ++loadSeq;
  state.loading = true;
  state.error = "";
  try {
    const data = await apiRequest<{ ok: boolean; items?: HistoryRoundItem[] }>("/api/result-history");
    if (seq !== loadSeq) return;
    state.items = data.items || [];
    state.loading = false;
  } catch (error) {
    if (seq !== loadSeq) return;
    state.loading = false;
    state.error = error instanceof Error ? error.message : "历史列表读取失败";
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
  state.detailLoading = true;
  state.detail = null;
  try {
    const detail = await apiRequest<HistoryRoundDetail>(`/api/result-history/${encodeURIComponent(runId)}`);
    state.detail = detail;
    state.open = false;
  } catch (error) {
    state.error = error instanceof Error ? error.message : "历史轮次读取失败";
    state.open = true;
  } finally {
    state.detailLoading = false;
  }
}

function backToLatest() {
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
    await apiRequest<{ ok: boolean }>(`/api/result-history/${encodeURIComponent(item.run_id)}`, {
      method: "DELETE",
    });
    if (state.detail?.source_run_id === item.run_id) {
      state.detail = null;
    }
    state.deleteTarget = null;
    await loadHistory();
  } catch (error) {
    state.error = error instanceof Error ? error.message : "删除失败";
  } finally {
    state.deleting = false;
  }
}

async function archiveLatest(): Promise<string[]> {
  const data = await apiRequest<{ ok: boolean; archived_run_ids?: string[] }>(
    "/api/result-history/archive-latest",
    { method: "POST" },
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
    archiveLatest,
  };
}

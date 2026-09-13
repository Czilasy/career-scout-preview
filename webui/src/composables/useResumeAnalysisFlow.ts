import { computed, ref, type Ref } from "vue";

import type { Notice, Platform, ResumeAnalysisPhase } from "../types";

/**
 * Spec041 B093：分析改为后台任务后，前端不再同步等一次请求。
 * 上传后（202 + task_id）与刷新接回走同一条任务状态路径，
 * 结果只经 applyAnalysisResult 投影一次。
 */
const POLL_INTERVAL_MS = 1000;
const POLL_MAX_ERRORS = 3;

export interface ResumeAnalysisInput {
  file: File;
  platform: Platform;
  aiConsent: boolean;
  profileId?: string;
}

export interface ResumeAnalysisTaskState {
  ok?: boolean;
  kind?: string;
  status?: string;
  error?: string;
  result?: unknown;
}

interface ResumeAnalysisRefs {
  activeStep: Ref<string>;
  uploadBusy: Ref<boolean>;
  resumeError: Ref<string>;
  resumeAnalysis: Ref<unknown>;
}

interface ResumeAnalysisApi {
  postAnalyzeResume: (form: FormData) => Promise<unknown>;
  /** 后台任务状态（Spec041）；缺省时后台模式退化为显式失败，不静默卡住。 */
  fetchTaskState?: (taskId: string) => Promise<ResumeAnalysisTaskState>;
  cancelActiveTasksForNewRound: () => Promise<boolean | void>;
  clearLatestResult: () => Promise<boolean | void>;
  enterSearchStep: () => void;
  notify: (msg: string, tone?: Notice["tone"]) => void;
}

export interface ResumeAnalysisFlowDeps {
  refs: ResumeAnalysisRefs;
  api: ResumeAnalysisApi;
  onAnalysisSuccess?: (data: unknown) => void;
  onAnalysisFailure?: (error: unknown) => void;
  /** 轮询间隔（测试可缩短）；默认 1000ms。 */
  pollIntervalMs?: number;
}

function realErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  if (typeof error === "string" && error.trim()) return error;
  if (error && typeof error === "object") {
    const message = (error as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  return "简历分析失败";
}

function readTaskId(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const value = (payload as { task_id?: unknown }).task_id;
  return typeof value === "string" ? value.trim() : "";
}

export function useResumeAnalysisFlow(deps: ResumeAnalysisFlowDeps) {
  const pollInterval = (): number => deps.pollIntervalMs ?? POLL_INTERVAL_MS;
  const phaseRef = ref<ResumeAnalysisPhase>("idle");
  let generation = 0;
  let returnRequested = false;
  let watchedTaskId = "";
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let pollErrorCount = 0;

  function enterSearchWhenAllowed(): void {
    if (phaseRef.value !== "succeeded") return;
    if (deps.refs.activeStep.value === "upload" || returnRequested) {
      returnRequested = false;
      deps.api.enterSearchStep();
    }
  }

  function onAnalysisComplete(): void {
    enterSearchWhenAllowed();
  }

  function stopWatching(): void {
    watchedTaskId = "";
    pollErrorCount = 0;
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function applyAnalysisResult(result: unknown): void {
    deps.refs.resumeAnalysis.value = result;
    deps.refs.uploadBusy.value = false;
    phaseRef.value = "succeeded";
    deps.onAnalysisSuccess?.(result);
    onAnalysisComplete();
  }

  function failAnalysis(message: string): void {
    deps.refs.uploadBusy.value = false;
    phaseRef.value = "failed";
    deps.refs.resumeError.value = message;
    deps.api.notify(message, "error");
  }

  function watchTask(taskId: string, requestGeneration: number): void {
    stopWatching();
    watchedTaskId = taskId;
    const tick = async (): Promise<void> => {
      if (watchedTaskId !== taskId || requestGeneration !== generation) return;
      const fetcher = deps.api.fetchTaskState;
      if (!fetcher) {
        stopWatching();
        failAnalysis("简历分析状态接口不可用");
        return;
      }
      try {
        const state = await fetcher(taskId);
        if (watchedTaskId !== taskId || requestGeneration !== generation) return;
        pollErrorCount = 0;
        const status = String(state?.status || "").toLowerCase();
        if (["done", "completed", "succeeded", "success"].includes(status)) {
          stopWatching();
          if (state?.result) applyAnalysisResult(state.result);
          else failAnalysis("简历分析完成但没有返回结果");
          return;
        }
        if (["failed", "error", "cancelled", "interrupted"].includes(status)) {
          stopWatching();
          failAnalysis(String(state?.error || "") || "简历分析失败");
          return;
        }
        deps.refs.uploadBusy.value = true;
        phaseRef.value = "analyzing";
        pollTimer = setTimeout(() => { void tick(); }, pollInterval());
      } catch (error) {
        if (watchedTaskId !== taskId || requestGeneration !== generation) return;
        pollErrorCount += 1;
        if (pollErrorCount >= POLL_MAX_ERRORS) {
          stopWatching();
          failAnalysis(realErrorMessage(error));
          return;
        }
        pollTimer = setTimeout(() => { void tick(); }, pollInterval());
      }
    };
    void tick();
  }

  async function runAnalysis(input: ResumeAnalysisInput, requestGeneration: number): Promise<void> {
    const form = new FormData();
    form.append("file", input.file);
    form.append("platform", input.platform);
    form.append("ai_consent", input.aiConsent ? "true" : "false");
    form.append("background", "true");
    if (input.profileId) form.append("profile_id", input.profileId);

    try {
      const cancelled = await deps.api.cancelActiveTasksForNewRound();
      if (cancelled === false) {
        phaseRef.value = "failed";
        deps.refs.resumeError.value = "旧任务未能安全结束，简历分析未开始";
        deps.api.notify(deps.refs.resumeError.value, "error");
        return;
      }
      const cleared = await deps.api.clearLatestResult();
      if (cleared === false) {
        phaseRef.value = "failed";
        deps.refs.resumeError.value = "旧结果未能安全归档，简历分析未开始";
        deps.api.notify(deps.refs.resumeError.value, "error");
        return;
      }
      if (requestGeneration !== generation) return;

      const result = await deps.api.postAnalyzeResume(form);
      if (requestGeneration !== generation) return;
      const taskId = readTaskId(result);
      if (taskId) {
        // 后台任务：从任务状态接回结果，与刷新恢复共用同一投影路径。
        watchTask(taskId, requestGeneration);
        return;
      }
      applyAnalysisResult(result);
    } catch (error) {
      if (requestGeneration !== generation) return;
      stopWatching();
      const message = realErrorMessage(error);
      failAnalysis(message);
      deps.onAnalysisFailure?.(error);
    } finally {
      if (requestGeneration === generation && !watchedTaskId) {
        deps.refs.uploadBusy.value = false;
      }
    }
  }

  function startAnalysis(input: ResumeAnalysisInput): void {
    generation += 1;
    returnRequested = false;
    stopWatching();
    deps.refs.resumeError.value = "";
    if (!input.file) {
      phaseRef.value = "failed";
      deps.refs.resumeError.value = "请先选择简历";
      return;
    }
    if (!input.aiConsent) {
      phaseRef.value = "failed";
      deps.refs.resumeError.value = "请先同意简历分析";
      return;
    }
    deps.refs.resumeAnalysis.value = null;
    phaseRef.value = "analyzing";
    deps.refs.uploadBusy.value = true;
    void runAnalysis(input, generation);
  }

  function landOnReturn(): void {
    returnRequested = true;
    if (phaseRef.value === "succeeded") {
      enterSearchWhenAllowed();
      return;
    }
    if (phaseRef.value === "idle") return;
    deps.refs.activeStep.value = "upload";
  }

  function reset(): void {
    generation += 1;
    returnRequested = false;
    stopWatching();
    phaseRef.value = "idle";
    deps.refs.uploadBusy.value = false;
    deps.refs.resumeError.value = "";
  }

  /**
   * 刷新后接回分析任务：taskId 非空时从任务状态取真实进度与结果，
   * 运行中继续等待、已完成直接投影、失败展示真实原因。
   */
  function restore(phase: ResumeAnalysisPhase, error = "", taskId = ""): void {
    generation += 1;
    returnRequested = false;
    stopWatching();
    phaseRef.value = phase;
    deps.refs.uploadBusy.value = phase === "analyzing";
    deps.refs.resumeError.value = error;
    if (taskId) {
      watchTask(taskId, generation);
    }
  }

  return {
    startAnalysis,
    phase: computed(() => phaseRef.value),
    landOnReturn,
    onAnalysisComplete,
    reset,
    restore,
  };
}

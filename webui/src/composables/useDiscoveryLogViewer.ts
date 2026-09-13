import { ref, watch } from "vue";

/** 历史轮条目里本对话框用到的字段（与该轮「查看运行日志」入口的上抛形状一致）。 */
export interface HistoryRoundLogItem {
  scrape_task_id?: string;
}

/**
 * Spec041 返工：日志对话框的现场（历史轮「查看运行日志」入口）。
 *
 * - `openForTask` 按指定任务打开运行日志（历史轮走这条，功能不变）；
 * - 切画像时关掉对话框并清掉任务号：旧画像的日志/轮询不得跟到新画像。
 */
export function useDiscoveryLogViewer(profileId: () => string) {
  const open = ref(false);
  const initialTaskId = ref("");

  function openForTask(item: HistoryRoundLogItem | string): void {
    const id = typeof item === "string" ? item.trim() : String(item?.scrape_task_id || "").trim();
    if (!id) return;
    initialTaskId.value = id;
    open.value = true;
  }

  function close(): void {
    open.value = false;
  }

  watch(profileId, () => {
    open.value = false;
    initialTaskId.value = "";
  });

  return { open, initialTaskId, openForTask, close };
}

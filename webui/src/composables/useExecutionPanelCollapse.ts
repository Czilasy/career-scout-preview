/**
 * 047 US3/FR-005：真实阶段进度首次出现时，把对应步骤的配置抽屉收拢。
 *
 * 只认「真实进度已出现」这一边沿：
 * - 02 抓取进度首次出现 → 收 `searchPanelsOpen` 与 `advancedPanelsOpen`；
 * - 03 筛选进度首次出现 → 收 `screenPanelOpen`；
 * - 同一 scene（画像 + 轮次）同一阶段只执行一次，不按每个 poll 反复写 false，
 *   所以用户在同阶段内手动重新展开不会被下一次轮询抢走；
 * - scene 身份切换（新一轮）后重新获得一次机会；
 * - 历史只读现场、只有 loading、请求被拒绝（进度从未出现）都不动作。
 *
 * 不新增偏好、不改 scene schema：收拢结果仍由 CollapsibleCard 写既有
 * cardOpenStates；helper 只负责在正确的边沿驱动既有 refs。
 */

import { watch, type Ref } from "vue";

export interface ExecutionPanelCollapseOptions {
  searchPanelsOpen: Ref<boolean>;
  advancedPanelsOpen: Ref<boolean>;
  screenPanelOpen: Ref<boolean>;
  hasScrapeProgress: Ref<boolean>;
  hasScreenProgress: Ref<boolean>;
  historyMode: Ref<boolean> | Readonly<Ref<boolean>>;
  /** 当前 scene 身份（画像 + runEpoch）；换轮后重新允许收拢一次。 */
  sceneKey: Ref<string> | Readonly<Ref<string>>;
}

type BoolSource =
  | Ref<boolean>
  | Readonly<Ref<boolean>>;

function read(source: BoolSource): boolean {
  return Boolean(source.value);
}

/**
 * 快照里是否已经出现「真实阶段进度」：只认进度条与计数里真实推进过的数字。
 *
 * 只看 `message`（启动占位/请求失败/准备中）不算进度；`overall_percent`
 * 兼容旧接口把 progress 直接写成数字百分比的形状。与 TaskProgress 的进度锚点
 * 同一批字段，不在这里另造第二套阶段权重。
 */
export function stageSnapshotHasProgress(
  snapshot: { progress?: unknown } | null | undefined,
): boolean {
  if (!snapshot) return false;
  const raw = snapshot.progress;
  if (typeof raw === "number") return Number.isFinite(raw) && raw > 0;
  const progress = raw && typeof raw === "object" && !Array.isArray(raw)
    ? raw as Record<string, unknown>
    : {};
  const value = (field: unknown): number => (
    typeof field === "number" && Number.isFinite(field) ? field : 0
  );
  return value(progress.overall_percent) > 0
    || value(progress.current) > 0
    || value(progress.total) > 0;
}

export function useExecutionPanelCollapse(options: ExecutionPanelCollapseOptions): void {
  let collapsedScene = "";
  let scrapeCollapsed = false;
  let screenCollapsed = false;

  function resetForScene(scene: string): void {
    if (scene === collapsedScene) return;
    collapsedScene = scene;
    scrapeCollapsed = false;
    screenCollapsed = false;
  }

  function settle(): void {
    if (read(options.historyMode)) return;
    resetForScene(String(options.sceneKey.value || ""));
    if (!scrapeCollapsed && read(options.hasScrapeProgress)) {
      options.searchPanelsOpen.value = false;
      options.advancedPanelsOpen.value = false;
      scrapeCollapsed = true;
    }
    if (!screenCollapsed && read(options.hasScreenProgress)) {
      options.screenPanelOpen.value = false;
      screenCollapsed = true;
    }
  }

  watch(
    [
      () => options.sceneKey.value,
      () => options.hasScrapeProgress.value,
      () => options.hasScreenProgress.value,
      () => options.historyMode.value,
    ],
    settle,
    { immediate: true, flush: "post" },
  );
}

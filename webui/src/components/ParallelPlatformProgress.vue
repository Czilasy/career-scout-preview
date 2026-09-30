<script setup lang="ts">
import TaskProgress from "./TaskProgress.vue";
import {
  ACTIVE_TRACK_STATUSES,
  STAGE_IN_FLIGHT_STATUSES,
  UNREADABLE_STAGE_STATUS,
  platformLabel,
  stageStatusLabel,
} from "../discovery";
import type { Platform } from "../types";
import type { TaskSnapshot } from "../types";

interface ParallelProgressItem {
  platform: Platform;
  trackId: string;
  kind: "scrape" | "screen";
  stage: "scrape" | "screen";
  runId?: string;
  status: string;
  snapshot: TaskSnapshot;
  enteredAt?: number;
  /**
   * 这张卡是否代表这条线当前所在的那一段（由 Flow 呈现层判定并下发）。
   * 缺省视为「是」，保持旧调用方口径不变；为「否」时轨道状态不再往本段快照上压，
   * 头部与卡体就都说本段那一句（拿不到证据时是兜底口径，见呈现层）。
   */
  carriesLineState?: boolean;
}

const props = defineProps<{
  items: ParallelProgressItem[];
  busyPlatform?: Platform | null;
  stale?: boolean;
}>();

const emit = defineEmits(["action", "action:pause", "action:resume", "action:stop"]);

// 轨道状态说「这一条线还活着吗」，阶段卡说「这一段跑到哪了」。
// 两者粒度不同：轨道状态覆盖抓取+筛选+结果整条线，一张卡只代表一个阶段。
// 一条线只有一个当前阶段：轨道级问题/终态只归那个阶段（carriesLineState），
// 其余阶段的卡只说自己的快照。动作按钮始终按轨道状态驱动，不随徽章变。
// 状态清单不在这里重复：在飞口径与轨道活动态口径的唯一一份在 discovery.ts。

function trackStatus(item: ParallelProgressItem): string {
  return item.status || item.snapshot.status || "";
}

function stageStatusOf(item: ParallelProgressItem): string {
  const snapshot = item.snapshot.status || "";
  const track = trackStatus(item);
  // 不承载轨道状态的卡只说自己那一段：轨道问题态不得压回它头上——它既不是当前段，
  // 又没有本段证据时呈现层已经把快照换成兜底口径，压回去就是替整条线背「已中断」。
  if (item.carriesLineState === false) return snapshot || UNREADABLE_STAGE_STATUS;
  // 轨道只能把一张卡往下压（暂停/终态），不能把已经跑完的阶段说成还在跑：
  // 快照仍写在飞态而整条线已经不在活动态时，以线为准。
  if (STAGE_IN_FLIGHT_STATUSES.includes(snapshot) && !ACTIVE_TRACK_STATUSES.includes(track)) {
    return track;
  }
  return snapshot || track;
}

function snapshotFor(item: ParallelProgressItem): TaskSnapshot {
  const stage = stageStatusOf(item);
  return item.snapshot.status === stage ? item.snapshot : { ...item.snapshot, status: stage };
}

// 徽章与卡体是同一张卡的同一句话：状态取 stageStatusOf（卡体拿到的就是它），
// 状态词与卡体走 discovery.ts 的同一份计算（含白箱完整性结论），因此同一张卡
// 不会头部说「完成，但有待确认」、卡体说「无法确认是否完成」。
function headerStatus(item: ParallelProgressItem): string {
  return stageStatusOf(item);
}

function headerStatusLabel(item: ParallelProgressItem): string {
  return stageStatusLabel(headerStatus(item), item.snapshot.integrity?.conclusion);
}

function stageLabel(item: ParallelProgressItem): string {
  return item.kind === "screen" ? "AI 筛选" : "抓取";
}

function onClick(event: Event, platform: Platform, action: "pause" | "resume" | "stop"): void {
  event.preventDefault();
  event.stopPropagation();
  emit("action", platform, action);
}

function visibleButtons(platform: Platform, status: string) {
  const active = ["queued", "running"].includes(status);
  const paused = ["paused", "interrupted"].includes(status);
  // 已中断的轨道没有活着的工人：停止注定送不达，只给恢复动作。
  return {
    pause: active,
    resume: paused,
    stop: active || status === "paused",
    busy: props.busyPlatform === platform,
    disabled: props.stale === true || props.busyPlatform === platform,
  };
}
</script>

<template>
  <section
    class="parallel-platform-progress"
    data-testid="parallel-platform-progress"
    aria-label="平台并行进度"
  >
    <article
      v-for="item in props.items"
      :key="`${item.platform}:${item.trackId}`"
      class="parallel-platform-item"
      :data-testid="`parallel-track-${item.platform}`"
    >
      <header class="parallel-item-header">
        <strong>{{ platformLabel(item.platform) }}</strong>
        <span class="parallel-item-stage">{{ stageLabel(item) }}</span>
        <span v-if="headerStatus(item)" data-testid="parallel-track-status">{{ headerStatusLabel(item) }}</span>
      </header>
      <TaskProgress
        :snapshot="snapshotFor(item)"
        :kind="item.kind"
        :task-id="item.runId || undefined"
        :platform="item.platform"
      />
      <div class="parallel-platform-actions">
        <button
          v-if="visibleButtons(item.platform, trackStatus(item)).pause"
          type="button"
           :disabled="visibleButtons(item.platform, trackStatus(item)).disabled"
          :data-testid="`parallel-${item.platform}-pause`"
          @click="onClick($event, item.platform, 'pause')"
        >暂停</button>
        <button
          v-if="visibleButtons(item.platform, trackStatus(item)).resume"
          type="button"
           :disabled="visibleButtons(item.platform, trackStatus(item)).disabled"
          :data-testid="`parallel-${item.platform}-resume`"
          @click="onClick($event, item.platform, 'resume')"
        >继续</button>
        <button
          v-if="visibleButtons(item.platform, trackStatus(item)).stop"
          type="button"
           :disabled="visibleButtons(item.platform, trackStatus(item)).disabled"
          :data-testid="`parallel-${item.platform}-stop`"
          @click="onClick($event, item.platform, 'stop')"
        >停止</button>
      </div>
    </article>
  </section>
</template>

<style scoped>
.parallel-platform-progress {
  display: grid;
  gap: 14px;
}
.parallel-platform-item {
  min-width: 0;
  padding: 10px 0;
}
.parallel-item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.parallel-item-header span {
  color: var(--muted);
  font-size: 12px;
}
.parallel-item-stage {
  margin-inline-end: auto;
  padding-inline-start: 8px;
}
.parallel-platform-actions {
  display: flex;
  gap: 8px;
}
.parallel-platform-actions button {
  border: 1px solid var(--hair);
  border-radius: 6px;
  padding: 4px 8px;
  background: var(--paper);
  cursor: pointer;
}
</style>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import { apiRequest, fetchLogs, type LogsResponse } from "../api";

const props = defineProps<{
  open: boolean;
  initialTaskId?: string;
  /** Spec041 返工：当前求职画像；运行日志只查本画像的任务，缺省时不查。 */
  profileId?: string;
}>();
const emit = defineEmits<{ close: [] }>();

/** 一行日志 = 稳定行号 + 文本。行号来自后端游标（文件行位置 / task_logs.seq），
 *  用于增量合并与渲染 key——相同文案的不同日志行各自独立，不做文案去重。 */
interface LogLine {
  no: number;
  text: string;
}

const lines = ref<LogLine[]>([]);
const startLine = ref(0);
const endLine = ref(0);
const identity = ref("");
const following = ref(true);
const loadingOlder = ref(false);
const loadingError = ref("");
const empty = ref(false);
// 035：日志视图模式（全局日志 / 运行日志）。
const mode = ref<"global" | "run">("global");
const runTaskId = ref("");

const scrollEl = ref<HTMLElement | null>(null);
let pollTimer: number | null = null;
let disposed = true;
// 视图代次：切模式 / 切任务 / 切画像 / 重开窗口都会 +1；旧请求响应按代次丢弃，
// 不把上一份日志、旧游标写回新视图。
let viewSeq = 0;
// 轮询在飞标记：请求慢于轮询间隔时不并发，避免同一区间被取两次。
let polling = false;

function scrollToBottom() {
  nextTick(() => {
    if (scrollEl.value) {
      scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
    }
  });
}

/** 后端返回的行 + start/end 游标 → 带稳定行号的展示行。 */
function numberedLines(data: LogsResponse): LogLine[] {
  const list = Array.isArray(data.lines) ? data.lines : [];
  const start = Number(data.start || 0);
  return list.map((text, index) => ({ no: start + index, text }));
}

/** 清空日志内容与游标，并作废在飞请求（切模式 / 切任务 / 重开窗口共用）。 */
function resetLogView(): void {
  viewSeq += 1;
  lines.value = [];
  startLine.value = 0;
  endLine.value = 0;
  identity.value = "";
  empty.value = false;
  loadingError.value = "";
  loadingOlder.value = false;
  following.value = true;
}

function stopPoll(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

/** 启动唯一轮询器：先停旧的再起新的，多次调用不会叠加定时器。 */
function startPoll(): void {
  stopPoll();
  pollTimer = window.setInterval(() => { void poll(); }, 2000);
}

async function resolveRunTask(): Promise<boolean> {
  // Spec041 返工：查询必须限定当前画像。缺画像时不查——宁可显示空态，
  // 也不静默回退到别的画像的最新任务。
  const profile = String(props.profileId || "").trim();
  if (!profile) return false;
  try {
    const data = await apiRequest<{ has_task?: boolean; task_id?: string }>(
      `/api/latest-running-task?profile_id=${encodeURIComponent(profile)}`,
    );
    if (data.has_task && data.task_id) {
      runTaskId.value = data.task_id;
      return true;
    }
  } catch { /* 忽略，回退空态 */ }
  return false;
}

/** 清掉运行日志的任务号、内容与轮询现场（切画像 / 关窗复用）。 */
function resetRunView(): void {
  runTaskId.value = "";
  resetLogView();
}

function resetForProfileChange(): void {
  resetRunView();
  if (props.open) emit("close");
}

async function switchMode(next: "global" | "run"): Promise<void> {
  if (mode.value === next) return;
  mode.value = next;
  // 两种日志各有自己的任务与游标，切换时彻底清掉，绝不复用对方区间。
  resetRunView();
  if (next === "run") {
    const seq = viewSeq;
    const ok = await resolveRunTask();
    if (seq !== viewSeq || !props.open) return;
    if (!ok) {
      empty.value = true;
      loadingError.value = "没有可查看的运行日志（暂无可关联的任务）";
      return;
    }
  }
  await loadTail();
}

/** 首屏 / 轮转重载：整段替换（不是追加），游标与行号一并重置。 */
async function loadTail(): Promise<void> {
  const seq = viewSeq;
  try {
    const data = await fetchLogs({
      tail: 500,
      task_id: mode.value === "run" ? runTaskId.value : undefined,
    });
    if (seq !== viewSeq) return;
    lines.value = numberedLines(data);
    startLine.value = Number(data.start || 0);
    endLine.value = Number(data.end || 0);
    identity.value = data.identity;
    empty.value = Boolean(data.empty);
    loadingError.value = "";
    following.value = true;
    scrollToBottom();
  } catch (error) {
    if (seq !== viewSeq) return;
    loadingError.value = error instanceof Error ? error.message : "日志加载失败";
  }
}

async function poll(): Promise<void> {
  if (disposed || !props.open || polling) return;
  if (mode.value === "run" && !runTaskId.value) return;
  polling = true;
  const seq = viewSeq;
  try {
    const data = await fetchLogs({
      since: endLine.value,
      identity: identity.value,
      task_id: mode.value === "run" ? runTaskId.value : undefined,
    });
    if (seq !== viewSeq || disposed) return;
    if (data.rotated) {
      // 日志轮转：整体重载尾部，保证实时更新不失效
      await loadTail();
      return;
    }
    // 只接受行号大于当前游标的新增行：后端同区间重发、乱序响应都不会重复渲染。
    const fresh = numberedLines(data).filter((item) => item.no > endLine.value);
    if (!fresh.length) return;
    lines.value = [...lines.value, ...fresh];
    endLine.value = Math.max(endLine.value, fresh[fresh.length - 1].no);
    if (data.identity) identity.value = data.identity;
    if (following.value) scrollToBottom();
  } catch {
    // 轮询失败静默，下轮自动重试
  } finally {
    polling = false;
  }
}

async function loadOlder(): Promise<void> {
  if (loadingOlder.value || startLine.value <= 1) return;
  loadingOlder.value = true;
  const seq = viewSeq;
  const before = scrollEl.value?.scrollHeight ?? 0;
  try {
    const data = await fetchLogs({
      offset: startLine.value,
      tail: 500,
      task_id: mode.value === "run" ? runTaskId.value : undefined,
    });
    if (seq !== viewSeq) return;
    // 与已有区间重叠的部分（行号 >= 当前起点）丢弃，加载更早只补旧行。
    const older = numberedLines(data).filter((item) => item.no < startLine.value);
    if (older.length) {
      lines.value = [...older, ...lines.value];
      startLine.value = older[0].no;
      await nextTick();
      // 保持视口位置：顶部插入内容后滚动增量补偿
      if (scrollEl.value) {
        scrollEl.value.scrollTop += scrollEl.value.scrollHeight - before;
      }
    }
  } catch {
    if (seq === viewSeq) loadingError.value = "加载更早日志失败";
  } finally {
    loadingOlder.value = false;
  }
}

function onScroll() {
  const el = scrollEl.value;
  if (!el) return;
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  following.value = atBottom;
  if (el.scrollTop <= 10) void loadOlder();
}

function goBottom() {
  following.value = true;
  scrollToBottom();
}

// Spec041 返工：切画像即清旧画像的运行日志现场（任务号/内容/轮询）并关窗，
// 不让旧画像的日志继续显示；历史轮按 initialTaskId 打开的功能不受影响。
watch(() => props.profileId, (profileId, previous) => {
  if (String(profileId || "") === String(previous || "")) return;
  resetForProfileChange();
});

// 指定任务变化（例如换了历史轮）：旧任务内容、游标、轮询彻底清掉再按新任务加载。
watch(() => props.initialTaskId, (taskId) => {
  const next = String(taskId || "").trim();
  if (!next) {
    if (!props.open) resetRunView();
    return;
  }
  resetLogView();
  runTaskId.value = next;
  mode.value = "run";
  if (props.open) void loadTail();
}, { immediate: true });

watch(
  () => props.open,
  (open) => {
    if (open) {
      disposed = false;
      resetLogView();
      // 035：外部指定任务（如历史轮的「查看运行日志」）→ 直接按该任务过滤运行日志。
      const initial = String(props.initialTaskId || "").trim();
      if (initial) {
        runTaskId.value = initial;
        mode.value = "run";
        void loadTail();
      } else if (mode.value === "run") {
        const seq = viewSeq;
        void resolveRunTask().then((ok) => {
          if (seq !== viewSeq) return;
          if (!ok) {
            empty.value = true;
            loadingError.value = "没有可查看的运行日志（暂无可关联的任务）";
          } else {
            void loadTail();
          }
        });
      } else {
        void loadTail();
      }
      startPoll();
    } else {
      disposed = true;
      polling = false;
      stopPoll();
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  disposed = true;
  stopPoll();
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="log-overlay"
      data-testid="log-viewer"
      @click.self="emit('close')"
    >
      <div class="log-dialog" role="dialog" aria-label="日志">
        <div class="log-header">
          <div class="log-mode" role="tablist" aria-label="日志视图">
            <button
              type="button"
              role="tab"
              :aria-selected="mode === 'global'"
              :class="['log-mode-btn', { active: mode === 'global' }]"
              data-testid="log-mode-global"
              @click="switchMode('global')"
            >全局日志</button>
            <button
              type="button"
              role="tab"
              :aria-selected="mode === 'run'"
              :class="['log-mode-btn', { active: mode === 'run' }]"
              data-testid="log-mode-run"
              @click="switchMode('run')"
            >运行日志</button>
          </div>
          <strong>{{ mode === "run" ? "运行日志（按任务过滤）" : "career-scout.log" }}</strong>
          <span class="log-meta">{{ lines.length }} 行</span>
          <button
            type="button"
            class="log-close"
            data-testid="log-close"
            aria-label="关闭日志"
            @click="emit('close')"
          >
            ×
          </button>
        </div>
        <div
          ref="scrollEl"
          class="log-body"
          data-testid="log-body"
          @scroll="onScroll"
        >
          <div v-if="loadingOlder" class="log-hint">加载更早日志…</div>
          <div v-if="loadingError" class="log-hint log-error">
            {{ loadingError }}
          </div>
          <div v-if="empty && !lines.length" class="log-hint">
            暂无日志（career-scout.log）
          </div>
          <pre v-else class="log-pre"><code
            v-for="line in lines"
            :key="line.no"
            class="log-line"
          >{{ line.text }}</code></pre>
        </div>
        <div class="log-footer">
          <button
            v-if="!following"
            type="button"
            class="log-go-bottom"
            data-testid="log-go-bottom"
            @click="goBottom"
          >
            回到底部
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.log-overlay {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
}

.log-dialog {
  display: flex;
  flex-direction: column;
  width: min(720px, calc(100vw - 32px));
  height: min(520px, calc(100vh - 80px));
  border: 1px solid #333;
  border-radius: 10px;
  background: #111;
  color: #d4d4d4;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
}

.log-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid #2a2a2a;
  font-family: ui-monospace, SFMono-Regular, Consolas, "Courier New", monospace;
}

.log-mode {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border: 1px solid #333;
  border-radius: 7px;
  background: #1a1a1a;
}

.log-mode-btn {
  appearance: none;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #888;
  font: inherit;
  font-size: 12px;
  padding: 3px 10px;
  cursor: pointer;
}

.log-mode-btn.active {
  background: #333;
  color: #fff;
}

.log-mode-btn:focus-visible {
  outline: 2px solid #666;
  outline-offset: 1px;
}

.log-meta {
  color: #888;
  font-size: 12px;
}

.log-close {
  margin-left: auto;
  border: 0;
  background: transparent;
  color: #aaa;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}

.log-close:hover {
  color: #fff;
}

.log-body {
  flex: 1;
  overflow-y: auto;
  padding: 10px 14px;
  background: #0d0d0d;
}

.log-hint {
  padding: 8px 0;
  color: #888;
  font-size: 13px;
  text-align: center;
}

.log-error {
  color: #e06c75;
}

.log-pre {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Consolas, "Courier New", monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.log-line {
  display: block;
}

.log-footer {
  min-height: 36px;
  padding: 6px 14px;
  border-top: 1px solid #2a2a2a;
  text-align: right;
}

.log-go-bottom {
  border: 1px solid #444;
  border-radius: 6px;
  background: #222;
  color: #d4d4d4;
  font: inherit;
  font-size: 13px;
  padding: 4px 12px;
  cursor: pointer;
}

.log-go-bottom:hover {
  background: #333;
}
</style>

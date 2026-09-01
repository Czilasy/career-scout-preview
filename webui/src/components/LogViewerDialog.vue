<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import { apiRequest, fetchLogs } from "../api";

const props = defineProps<{ open: boolean; initialTaskId?: string }>();
const emit = defineEmits<{ close: [] }>();

const lines = ref<string[]>([]);
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
let disposed = false;

function scrollToBottom() {
  nextTick(() => {
    if (scrollEl.value) {
      scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
    }
  });
}

async function resolveRunTask(): Promise<boolean> {
  try {
    const data = await apiRequest<{ has_task?: boolean; task_id?: string }>("/api/latest-running-task");
    if (data.has_task && data.task_id) {
      runTaskId.value = data.task_id;
      return true;
    }
  } catch { /* 忽略，回退空态 */ }
  return false;
}

async function switchMode(next: "global" | "run"): Promise<void> {
  if (mode.value === next) return;
  mode.value = next;
  lines.value = [];
  startLine.value = 0;
  endLine.value = 0;
  identity.value = "";
  empty.value = false;
  loadingError.value = "";
  if (next === "run") {
    const ok = await resolveRunTask();
    if (!ok) {
      empty.value = true;
      loadingError.value = "没有可查看的运行日志（暂无可关联的任务）";
      return;
    }
  }
  await loadTail();
}

async function loadTail() {
  try {
    const data = await fetchLogs({
      tail: 500,
      task_id: mode.value === "run" ? runTaskId.value : undefined,
    });
    lines.value = data.lines;
    startLine.value = data.start;
    endLine.value = data.end;
    identity.value = data.identity;
    empty.value = Boolean(data.empty);
    loadingError.value = "";
    following.value = true;
    scrollToBottom();
  } catch (error) {
    loadingError.value = error instanceof Error ? error.message : "日志加载失败";
  }
}

async function poll() {
  if (disposed || !props.open) return;
  if (mode.value === "run" && !runTaskId.value) return;
  try {
    const data = await fetchLogs({
      since: endLine.value,
      identity: identity.value,
      task_id: mode.value === "run" ? runTaskId.value : undefined,
    });
    if (data.rotated) {
      // 日志轮转：整体重载尾部，保证实时更新不失效
      await loadTail();
      return;
    }
    if (data.lines.length) {
      lines.value = [...lines.value, ...data.lines];
      endLine.value = data.end;
      identity.value = data.identity;
      if (following.value) scrollToBottom();
    }
  } catch {
    // 轮询失败静默，下轮自动重试
  }
}

async function loadOlder() {
  if (loadingOlder.value || startLine.value <= 1) return;
  loadingOlder.value = true;
  const before = scrollEl.value?.scrollHeight ?? 0;
  try {
    const data = await fetchLogs({
      offset: startLine.value,
      tail: 500,
      task_id: mode.value === "run" ? runTaskId.value : undefined,
    });
    if (data.lines.length) {
      lines.value = [...data.lines, ...lines.value];
      startLine.value = data.start;
      await nextTick();
      // 保持视口位置：顶部插入内容后滚动增量补偿
      if (scrollEl.value) {
        scrollEl.value.scrollTop += scrollEl.value.scrollHeight - before;
      }
    }
  } catch {
    loadingError.value = "加载更早日志失败";
  } finally {
    loadingOlder.value = false;
  }
}

function onScroll() {
  const el = scrollEl.value;
  if (!el) return;
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  following.value = atBottom;
  if (el.scrollTop <= 10) loadOlder();
}

function goBottom() {
  following.value = true;
  scrollToBottom();
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      disposed = false;
      lines.value = [];
      startLine.value = 0;
      endLine.value = 0;
      identity.value = "";
      loadingError.value = "";
      // 035：外部指定任务（如历史轮的「查看运行日志」）→ 直接按该任务过滤运行日志。
      if (props.initialTaskId) {
        runTaskId.value = props.initialTaskId;
        mode.value = "run";
        loadTail();
      } else if (mode.value === "run") {
        void resolveRunTask().then((ok) => {
          if (!ok) {
            empty.value = true;
            loadingError.value = "没有可查看的运行日志（暂无可关联的任务）";
          } else {
            void loadTail();
          }
        });
      } else {
        loadTail();
      }
      pollTimer = window.setInterval(poll, 2000);
    } else {
      disposed = true;
      if (pollTimer !== null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  disposed = true;
  if (pollTimer !== null) window.clearInterval(pollTimer);
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
            v-for="(line, index) in lines"
            :key="index"
            class="log-line"
          >{{ line }}</code></pre>
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

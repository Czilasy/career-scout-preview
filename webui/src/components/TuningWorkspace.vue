<script setup lang="ts">
import { computed } from "vue";
import {
  CheckCircle2,
  Clock3,
  FileSearch,
  LoaderCircle,
  Play,
  Plus,
  RotateCcw,
  ShieldAlert,
  XCircle,
} from "@lucide/vue";
import type { TuningExperiment } from "../types";

const props = withDefaults(defineProps<{
  experiment: TuningExperiment | null;
  loading?: boolean;
  busy?: boolean;
  canRollback?: boolean;
}>(), {
  loading: false,
  busy: false,
  canRollback: false,
});

const emit = defineEmits<{
  create: [];
  confirm: [];
  cancel: [];
  resume: [];
  refresh: [];
  apply: [];
  rollback: [];
}>();

const statusLabel = computed(() => ({
  draft: "待确认输入",
  preflight: "环境检查",
  awaiting_instruction: "等待任务单",
  queued: "已排队",
  running: "执行中",
  evaluating: "评估中",
  blocked: "已阻断",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
})[props.experiment?.status || "draft"]);

const remainingLabel = computed(() => {
  const seconds = props.experiment?.progress.estimated_remaining_seconds;
  if (typeof seconds !== "number" || seconds < 0) return "待程序计算";
  if (seconds < 60) return `${Math.round(seconds)}秒`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)}分钟`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.ceil((seconds % 3600) / 60);
  return minutes ? `${hours}小时${minutes}分钟` : `${hours}小时`;
});

const evidence = computed(() => props.experiment?.evidence || []);
</script>

<template>
  <section class="tuning-workspace" aria-labelledby="tuning-title">
    <header class="tuning-heading">
      <div>
        <span class="tuning-kicker">深度实验</span>
        <h3 id="tuning-title">高级设置调优</h3>
      </div>
      <span v-if="experiment" class="tuning-status" :data-status="experiment.status">
        {{ statusLabel }}
      </span>
    </header>

    <div v-if="loading" class="tuning-state" data-testid="tuning-loading" aria-live="polite">
      <LoaderCircle class="spin" :size="20" aria-hidden="true" />
      <span>正在读取实验状态</span>
    </div>

    <div v-else-if="!experiment" class="tuning-state tuning-empty" data-testid="tuning-empty">
      <FileSearch :size="22" aria-hidden="true" />
      <div><strong>尚未创建深度实验</strong><span>固定输入确认后才会进入串行执行。</span></div>
      <button class="button secondary" type="button" data-testid="create-tuning" :disabled="busy" @click="emit('create')">
        <Plus :size="16" aria-hidden="true" />创建实验
      </button>
    </div>

    <template v-else>
      <div v-if="experiment.status === 'blocked'" class="tuning-block" data-testid="tuning-block" role="alert">
        <ShieldAlert :size="20" aria-hidden="true" />
        <div><strong>{{ experiment.blocked_code || "experiment_blocked" }}</strong><span>{{ experiment.blocked_reason || "程序未提供阻断说明" }}</span></div>
      </div>
      <div v-else-if="experiment.status === 'failed'" class="tuning-block failed" role="alert">
        <XCircle :size="20" aria-hidden="true" />
        <div><strong>实验失败</strong><span>{{ experiment.blocked_reason || "请查看程序证据" }}</span></div>
      </div>
      <div v-else-if="experiment.status === 'completed'" class="tuning-complete" role="status">
        <CheckCircle2 :size="20" aria-hidden="true" />完整结果已通过后端门禁，可审阅后整体应用。
      </div>

      <div class="tuning-metrics">
        <div><span>当前阶段</span><strong data-testid="tuning-stage">{{ experiment.current_stage || "未开始" }}</strong></div>
        <div><span>当前候选</span><strong data-testid="tuning-candidate">{{ experiment.current_candidate_id || "无" }}</strong></div>
        <div><span>当前轮次</span><strong data-testid="tuning-round">{{ experiment.current_round_id || "无" }}</strong></div>
        <div><span>预计剩余</span><strong data-testid="tuning-remaining"><Clock3 :size="14" aria-hidden="true" />{{ remainingLabel }}</strong></div>
      </div>

      <div class="tuning-progress" data-testid="tuning-progress">
        <div>
          <span>已确认 {{ experiment.progress.confirmed_rounds }} 轮</span>
          <span>剩余 {{ experiment.progress.remaining_required_rounds }} 轮</span>
        </div>
        <progress
          :value="experiment.progress.confirmed_rounds"
          :max="Math.max(1, experiment.progress.confirmed_rounds + experiment.progress.remaining_required_rounds)"
        />
        <small v-if="experiment.progress.invalid_rounds">无效轮次 {{ experiment.progress.invalid_rounds }}，不计入结果</small>
      </div>

      <details class="tuning-evidence">
        <summary>程序证据（{{ evidence.length }}）</summary>
        <p v-if="!evidence.length">尚无已公开证据。</p>
        <dl v-else>
          <template v-for="(item, index) in evidence" :key="String(item.id || index)">
            <dt>{{ item.id || `evidence-${index + 1}` }}</dt>
            <dd>{{ item.status || "recorded" }}<span v-if="item.total_duration_ms"> · {{ item.total_duration_ms }} ms</span></dd>
          </template>
        </dl>
      </details>

      <div class="tuning-actions">
        <button v-if="experiment.status === 'draft'" class="button primary" type="button" :disabled="busy" @click="emit('confirm')">
          <Play :size="16" aria-hidden="true" />确认固定输入
        </button>
        <button v-if="experiment.can_resume" class="button primary" type="button" data-testid="resume-tuning" :disabled="busy" @click="emit('resume')">
          <Play :size="16" aria-hidden="true" />继续实验
        </button>
        <button v-if="experiment.status === 'completed' && experiment.can_apply" class="button primary" type="button" data-testid="apply-tuning" :disabled="busy" @click="emit('apply')">
          <CheckCircle2 :size="16" aria-hidden="true" />整体应用
        </button>
        <button v-if="canRollback" class="button secondary" type="button" data-testid="rollback-tuning" :disabled="busy" @click="emit('rollback')">
          <RotateCcw :size="16" aria-hidden="true" />回退上一版本
        </button>
        <button class="button secondary" type="button" :disabled="busy" @click="emit('refresh')">
          <RotateCcw :size="16" aria-hidden="true" />刷新状态
        </button>
        <button v-if="experiment.can_cancel" class="button danger" type="button" :disabled="busy" @click="emit('cancel')">
          <XCircle :size="16" aria-hidden="true" />取消实验
        </button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.tuning-workspace { display: grid; gap: 14px; min-width: 0; }
.tuning-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.tuning-heading h3 { margin: 3px 0 0; font-size: 18px; letter-spacing: 0; }
.tuning-kicker { color: var(--accent); font-size: 11px; font-weight: 800; text-transform: uppercase; }
.tuning-status { padding: 5px 9px; border: 1px solid var(--line-strong); border-radius: 999px; color: var(--text-soft); font-size: 11px; font-weight: 750; }
.tuning-status[data-status="blocked"], .tuning-status[data-status="failed"] { color: var(--danger); }
.tuning-status[data-status="completed"] { color: var(--success); }
.tuning-state { display: flex; align-items: center; gap: 10px; min-height: 76px; color: var(--muted); }
.tuning-empty { flex-wrap: wrap; }
.tuning-empty div { display: grid; flex: 1 1 220px; gap: 3px; }
.tuning-empty strong { color: var(--text); }
.tuning-empty span { font-size: 12px; }
.tuning-block, .tuning-complete { display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: start; gap: 10px; padding: 12px 14px; border-left: 3px solid var(--warning); background: rgb(244 199 107 / 7%); }
.tuning-block.failed { border-color: var(--danger); }
.tuning-block div { display: grid; gap: 3px; }
.tuning-block span { color: var(--text-soft); font-size: 12px; line-height: 1.5; }
.tuning-complete { border-color: var(--success); color: var(--text-soft); }
.tuning-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-block: 1px solid var(--line); }
.tuning-metrics > div { display: grid; gap: 4px; min-width: 0; padding: 12px; border-right: 1px solid var(--line); }
.tuning-metrics > div:last-child { border-right: 0; }
.tuning-metrics span { color: var(--muted); font-size: 11px; }
.tuning-metrics strong { display: flex; align-items: center; gap: 5px; min-width: 0; overflow: hidden; color: var(--text-soft); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.tuning-progress { display: grid; gap: 7px; }
.tuning-progress > div { display: flex; justify-content: space-between; gap: 12px; color: var(--text-soft); font-size: 12px; }
.tuning-progress progress { width: 100%; height: 7px; accent-color: var(--accent); }
.tuning-progress small { color: var(--warning); }
.tuning-evidence { border-top: 1px solid var(--line); padding-top: 10px; }
.tuning-evidence summary { min-height: 36px; color: var(--text-soft); cursor: pointer; }
.tuning-evidence p { color: var(--muted); font-size: 12px; }
.tuning-evidence dl { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 7px 14px; margin: 8px 0 0; font-size: 12px; }
.tuning-evidence dt { min-width: 0; overflow: hidden; color: var(--text-soft); text-overflow: ellipsis; }
.tuning-evidence dd { margin: 0; color: var(--muted); }
.tuning-actions { display: flex; flex-wrap: wrap; gap: 8px; }
@media (max-width: 700px) {
  .tuning-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .tuning-metrics > div:nth-child(2) { border-right: 0; }
  .tuning-metrics > div:nth-child(-n+2) { border-bottom: 1px solid var(--line); }
}
</style>

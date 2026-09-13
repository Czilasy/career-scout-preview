<script setup lang="ts">
import { computed } from "vue";
import BaseDialog from "./BaseDialog.vue";

/** 批内二选一的使用场景：暂停任务 / 结束并保存结果。 */
type ChoiceKind = "pause" | "finish";

const props = withDefaults(defineProps<{
  open: boolean;
  batchInfo: { current: number; total: number } | null;
  kind?: ChoiceKind;
  title?: string;
  description?: string;
  immediateLabel?: string;
  gracefulLabel?: string;
  /** 默认聚焦目标；缺省落右上 ✕（等于什么都不做，回车不会误伤这一批）。 */
  focus?: "immediate" | "graceful" | "cancel";
}>(), {
  kind: "pause",
  title: "",
  description: "",
  immediateLabel: "",
  gracefulLabel: "",
  focus: undefined,
});

const emit = defineEmits<{
  (e: "close"): void;
  (e: "choose", mode: "immediate" | "graceful"): void;
}>();

// 迷你档（size="xs"）：一行标题 + 一行说明 + 两个小键，没有底部按钮条
// （取消 = 右上 ✕ / Esc）。语气保持平实：不出现"警告/危险"字样。
const COPY: Record<ChoiceKind, {
  title: string;
  line: string;
  gracefulLabel: string;
  immediateLabel: string;
}> = {
  pause: {
    title: "暂停 AI 筛选",
    line: "现在停，这一批要重抓",
    gracefulLabel: "等这批抓完",
    immediateLabel: "立即停止",
  },
  finish: {
    title: "结束并保存结果",
    line: "立即保存只存已落盘的",
    gracefulLabel: "等这批抓完再保存",
    immediateLabel: "立即保存",
  },
};

/** 第几批写进说明行（"第 2 批 / 共 4 批 · 现在停，这一批要重抓"），不单独占一行。 */
const batchText = computed(() => {
  const info = props.batchInfo;
  if (!info || info.total <= 0) return "";
  return info.total > 1 ? `第 ${info.current} 批 / 共 ${info.total} 批` : "当前只有这一批";
});

const copy = computed(() => {
  const base = COPY[props.kind];
  return {
    title: props.title || base.title,
    line: props.description || [batchText.value, base.line].filter(Boolean).join(" · "),
    gracefulLabel: props.gracefulLabel || base.gracefulLabel,
    immediateLabel: props.immediateLabel || base.immediateLabel,
  };
});

const initialFocus = computed(() => {
  if (props.focus === "graceful") return "[data-testid='pause-graceful']";
  if (props.focus === "immediate") return "[data-testid='pause-immediate']";
  return ".dialog-header .icon-button";
});
</script>

<template>
  <!-- 025 B076 批中二选一（暂停 / 结束保存共用）。
       复审重做：暂停是轻动作，收成迷你档（dialog-xs / 380px）——
       一行标题 + 一行说明 + 两个小键；取消用右上 ✕ 或 Esc，不再单占一条底部栏。 -->
  <BaseDialog
    :open="open"
    :title="copy.title"
    :description="copy.line"
    size="xs"
    :initial-focus="initialFocus"
    id="pause-batch-choice"
    data-testid="pause-batch-dialog"
    @close="emit('close')"
  >
    <div class="pause-batch-actions">
      <button
        type="button"
        class="pause-batch-option is-graceful"
        data-testid="pause-graceful"
        @click="emit('choose', 'graceful')"
      >
        {{ copy.gracefulLabel }}
      </button>
      <button
        type="button"
        class="pause-batch-option is-immediate"
        data-testid="pause-immediate"
        @click="emit('choose', 'immediate')"
      >
        {{ copy.immediateLabel }}
      </button>
    </div>
  </BaseDialog>
</template>

<style scoped>
.pause-batch-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.pause-batch-option {
  min-height: 32px;
  padding: 0 12px;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  border-radius: 8px;
  cursor: pointer;
  transition: border-color .15s ease, background-color .15s ease, color .15s ease;
}
/* 稳妥项：主色淡底 + 主色描边（不铺整块实色） */
.pause-batch-option.is-graceful {
  color: var(--brand-ink);
  background: var(--brand-wash);
  border: 1px solid var(--brand-edge);
}
.pause-batch-option.is-graceful:hover {
  border-color: var(--brand);
}
/* 有损失的那项：文字用危险色，底色保持中性 */
.pause-batch-option.is-immediate {
  color: var(--reject);
  background: transparent;
  border: 1px solid var(--hair);
}
.pause-batch-option.is-immediate:hover {
  border-color: var(--reject-edge);
  background: var(--reject-wash);
}
</style>

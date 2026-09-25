<script setup lang="ts">
import { computed } from "vue";
import { LoaderCircle } from "@lucide/vue";
import BaseDialog from "./BaseDialog.vue";

/** 使用场景：暂停任务 / 结束并保存结果 / 关闭前确认（045 v2）。 */
type ChoiceKind = "pause" | "finish" | "close" | "close_save";

const props = withDefaults(defineProps<{
  open: boolean;
  batchInfo: { current: number; total: number } | null;
  kind?: ChoiceKind;
  title?: string;
  description?: string;
  immediateLabel?: string;
  gracefulLabel?: string;
  /** 收尾进行中：动作键禁用并显示等待指示（045 v2 FR-012）。 */
  busy?: boolean;
  /** 默认聚焦目标；缺省落右上 ✕（等于什么都不做，回车不会误伤这一批）。 */
  focus?: "immediate" | "graceful" | "cancel";
}>(), {
  kind: "pause",
  title: "",
  description: "",
  immediateLabel: "",
  gracefulLabel: "",
  busy: false,
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
  // 045 v2：关窗场景只给「立即结束」一个出口，不提供等待类选项——用户点
  // 关闭就说明他着急。gracefulLabel 留空即不渲染该键（见模板 v-if）。
  close: {
    title: "还有任务正在进行中",
    line: "立即结束只保存已抓到的，这一批会丢弃；不着急可以先暂停，落盘后再关闭",
    gracefulLabel: "",
    immediateLabel: "立即结束",
  },
  // 045 v2：未运行（暂停/报错/中断）关窗沿用 v1 口径——「结束并保存 / 取消」。
  // 流程本就没在跑，没有"这一批"可丢，动作键是无损的，用稳妥色。
  close_save: {
    title: "结束并保存结果",
    line: "保存后这一轮进历史，可随时回看",
    gracefulLabel: "",
    immediateLabel: "结束并保存",
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

/** 收尾进行中不再接受动作键点击，避免两条收尾路径打架（045 v2）。 */
function onImmediate() {
  if (props.busy) return;
  emit("choose", "immediate");
}

/** 动作键色调：结束保存是无损动作走稳妥色；立即结束有丢弃损失走危险色。 */
const immediateTone = computed(() =>
  props.kind === "close_save" ? "is-graceful" : "is-immediate",
);
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
        v-if="copy.gracefulLabel"
        type="button"
        class="pause-batch-option is-graceful"
        data-testid="pause-graceful"
        :disabled="busy"
        @click="emit('choose', 'graceful')"
      >
        {{ copy.gracefulLabel }}
      </button>
      <button
        type="button"
        class="pause-batch-option"
        :class="immediateTone"
        data-testid="pause-immediate"
        :disabled="busy"
        @click="onImmediate"
      >
        <LoaderCircle v-if="busy" class="spin" :size="15" />
        <span>{{ copy.immediateLabel }}</span>
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
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 12px;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  border-radius: 8px;
  cursor: pointer;
  transition: border-color .15s ease, background-color .15s ease, color .15s ease;
}
/* 045 v2：收尾等待中两个出口都禁用，右上 ✕ 仍可用来取消 */
.pause-batch-option:disabled {
  opacity: .55;
  cursor: default;
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

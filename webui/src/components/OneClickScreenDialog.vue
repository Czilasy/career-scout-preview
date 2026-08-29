<script lang="ts">
/** 跨平台去重开关的本地记忆键（019，contracts §3：默认开）。 */
const DEDUPE_STORAGE_KEY = "cross_platform_dedupe_enabled";

/** 读取跨平台去重开关（供提交筛选时携带；记忆于 localStorage）。 */
export function crossPlatformDedupeEnabled(): boolean {
  try {
    return window.localStorage.getItem(DEDUPE_STORAGE_KEY) !== "false";
  } catch {
    return true;
  }
}
</script>

<script setup lang="ts">
import { ref, watch } from "vue";
import { Sparkles } from "@lucide/vue";
import BaseDialog from "./BaseDialog.vue";
import { singleSelectNextValue } from "../discovery";
import type { Platform } from "../types";

export interface OneClickFilterGroup {
  key: string;
  label: string;
  /** 028：单选字段（第 7 类招聘者上次活跃）点新值替换、点已选值取消。 */
  multiple?: boolean;
  sentinel: { label: string; code: string } | null;
  options: Array<[string, string]>;
}

const props = defineProps<{
  open: boolean;
  platform: Platform;
  groups: OneClickFilterGroup[];
  modelValue: Record<string, string[]>;
  hasOldResult: boolean;
}>();

const emit = defineEmits<{
  close: [];
  confirm: [fields: Record<string, string[]>];
}>();

const values = ref<Record<string, string[]>>({});

// 019：「跨平台去重」开关（默认开，localStorage 记忆；随提交携带）。
const dedupeEnabled = ref(crossPlatformDedupeEnabled());
watch(dedupeEnabled, (enabled) => {
  try {
    window.localStorage.setItem(DEDUPE_STORAGE_KEY, String(enabled));
  } catch { /* 记忆失败不阻断提交 */ }
});

function syncValues() {
  values.value = Object.fromEntries(
    Object.entries(props.modelValue || {}).map(([key, list]) => [
      key,
      Array.isArray(list) ? [...list] : [],
    ]),
  );
}
syncValues();

watch(() => props.open, (open) => {
  if (open) syncValues();
});

watch(
  () => [props.platform, props.modelValue] as const,
  () => {
    if (props.open) syncValues();
  },
  { deep: true },
);

function toggle(key: string, code: string) {
  const current = values.value[key] || [];
  // 028：单选字段点新值替换、点已选值取消；多选字段维持增删。
  const group = props.groups.find((group) => group.key === key);
  const single = singleSelectNextValue(group?.multiple, current, code);
  if (single !== null) {
    values.value[key] = single;
    return;
  }
  values.value[key] = current.includes(code)
    ? current.filter((item) => item !== code)
    : [...current, code];
}

function clearGroup(key: string) {
  values.value[key] = [];
}

function confirm() {
  emit("confirm", Object.fromEntries(
    Object.entries(values.value).map(([key, list]) => [key, [...list]]),
  ));
}
</script>

<template>
  <BaseDialog
    id="one-click-screen"
    :open="open"
    title="开始筛选并 AI 优化"
    description="确认筛选条件后，将先抓取岗位，再自动进行 AI 筛选。"
    size="lg"
    @close="$emit('close')"
  >
    <p
      v-if="hasOldResult"
      class="one-click-replace-hint"
      data-testid="one-click-old-result-hint"
      role="status"
    >
      将开始新一轮，当前结果会被替换
    </p>

    <div class="one-click-filter-groups">
      <fieldset
        v-for="group in groups"
        :key="group.key"
        class="filter-group one-click-filter-group"
      >
        <legend>{{ group.label }}</legend>
        <div class="chip-grid compact">
          <button
            v-if="group.sentinel"
            class="choice-chip"
            :class="{ selected: !(values[group.key] || []).length }"
            type="button"
            :aria-pressed="!(values[group.key] || []).length"
            @click="clearGroup(group.key)"
          >{{ group.sentinel.label }}</button>
          <button
            v-for="([label, code]) in group.options"
            :key="code"
            class="choice-chip"
            :class="{ selected: (values[group.key] || []).includes(code) }"
            type="button"
            :aria-pressed="(values[group.key] || []).includes(code)"
            @click="toggle(group.key, code)"
          >{{ label }}</button>
        </div>
      </fieldset>
      <p v-if="!groups.length" class="one-click-filter-empty">
        当前平台暂无筛选条件，可直接开始。
      </p>
    </div>

    <label class="one-click-dedupe-toggle" data-testid="one-click-dedupe-toggle">
      <input
        v-model="dedupeEnabled"
        type="checkbox"
        data-testid="one-click-dedupe-checkbox"
      />
      <span>跨平台去重：另一平台已筛过的相同岗位不再重复筛选</span>
    </label>

    <template #footer>
      <button
        type="button"
        class="button secondary"
        data-testid="one-click-cancel"
        @click="$emit('close')"
      >取消</button>
      <button
        type="button"
        class="button primary"
        data-testid="one-click-confirm"
        @click="confirm"
      >
        <Sparkles :size="17" aria-hidden="true" />开始筛选并 AI 优化
      </button>
    </template>
  </BaseDialog>
</template>

<style scoped>
.one-click-replace-hint {
  margin: 0 0 14px;
  padding: 10px 12px;
  border: 1px solid var(--unsure-edge, var(--hair));
  border-radius: 8px;
  color: var(--unsure-deep);
  background: var(--unsure-wash);
  font-size: 13px;
  line-height: 1.5;
}
.one-click-filter-groups {
  display: grid;
  gap: 14px;
}
.one-click-filter-group {
  margin: 0;
  padding: 0;
  border: 0;
}
.one-click-filter-group legend {
  margin-bottom: 8px;
  color: var(--ink-1);
  font-size: 13px;
  font-weight: 700;
}
.one-click-filter-empty {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}
.one-click-dedupe-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 2px 0 -10px;
  font-size: 13px;
  color: var(--ink-1);
  cursor: pointer;
  user-select: none;
}
.one-click-dedupe-toggle input {
  accent-color: var(--accent);
}
</style>

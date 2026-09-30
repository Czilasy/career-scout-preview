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
import { computed, ref, watch } from "vue";
import { Sparkles } from "@lucide/vue";
import BaseDialog from "./BaseDialog.vue";
import { platformLabel, singleSelectNextValue } from "../discovery";
import { UNIFIED_FILTER_SCHEMA } from "../parallelFilterMapping";
import type { Platform, UnifiedFilterField, UnifiedFilterValues } from "../types";

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
  /** B096: optional two-panel mode; omitted keeps the legacy single panel. */
  mode?: "single" | "all";
  platforms?: Platform[];
  platformGroups?: Partial<Record<Platform, OneClickFilterGroup[]>>;
  platformModelValues?: Partial<Record<Platform, Record<string, string[]>>>;
  unifiedValues?: UnifiedFilterValues;
  confirmDisabled?: boolean;
  /** The dialog is visible before async Flow/schema preparation completes. */
  preparing?: boolean;
  /** A Flow start request is in flight; prevent duplicate submissions. */
  loading?: boolean;
  errorMessage?: string;
}>();

const emit = defineEmits<{
  close: [];
  confirm: [fields: Record<string, string[]>];
  "parallel-confirm": [
    fields: Record<Platform, Record<string, string[]>>,
  ];
  "unified-change": [field: UnifiedFilterField, values: string[]];
  "platform-change": [platform: Platform, field: string, values: string[]];
  "confirm-blocked": [platforms: Platform[]];
}>();

const values = ref<Record<string, string[]>>({});
const parallelValues = ref<Record<Platform, Record<string, string[]>>>({
  boss: {},
  zhilian: {},
});
type ParallelPage = "unified" | Platform;
const activeParallelPage = ref<ParallelPage>("unified");
const activeParallelPlatform = computed<Platform>(() =>
  activeParallelPage.value === "boss" ? "boss" : "zhilian");
const isUnifiedPage = computed(() => activeParallelPage.value === "unified");
const isParallel = computed(() => props.mode === "all");
const parallelPlatforms = computed<Platform[]>(() => {
  const configured = props.platforms?.filter((item): item is Platform =>
    item === "boss" || item === "zhilian",
  );
  return configured?.length ? configured : ["boss", "zhilian"];
});
const unifiedGroupEntries = Object.entries(UNIFIED_FILTER_SCHEMA) as Array<
  [UnifiedFilterField, { label: string; options: Array<[string, string]> }]
>;
const visibleGroups = computed(() => isParallel.value && !isUnifiedPage.value
  ? (props.platformGroups?.[activeParallelPlatform.value] || [])
  : props.groups);
const visibleValues = computed(() => isParallel.value && !isUnifiedPage.value
  ? (parallelValues.value[activeParallelPlatform.value] || {})
  : values.value);

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

function syncParallelValues() {
  const next = { boss: {}, zhilian: {} } as Record<Platform, Record<string, string[]>>;
  for (const platform of parallelPlatforms.value) {
    const source = props.platformModelValues?.[platform] || {};
    next[platform] = Object.fromEntries(
      Object.entries(source).map(([key, list]) => [key, Array.isArray(list) ? [...list] : []]),
    );
  }
  parallelValues.value = next;
}
syncParallelValues();

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

watch(
  () => [props.platformModelValues, props.platformGroups] as const,
  () => {
    if (props.open && isParallel.value) syncParallelValues();
  },
  { deep: true },
);

function currentValues(): Record<string, string[]> {
  return isParallel.value ? visibleValues.value : values.value;
}

function emitPlatformChange(field: string) {
  if (!isParallel.value || isUnifiedPage.value) return;
  emit("platform-change", activeParallelPlatform.value, field, [
    ...(currentValues()[field] || []),
  ]);
}

function toggle(key: string, code: string) {
  const current = currentValues()[key] || [];
  // 028：单选字段点新值替换、点已选值取消；多选字段维持增删。
  const group = visibleGroups.value.find((group) => group.key === key);
  const single = singleSelectNextValue(group?.multiple, current, code);
  if (single !== null) {
    currentValues()[key] = single;
    emitPlatformChange(key);
    return;
  }
  currentValues()[key] = current.includes(code)
    ? current.filter((item) => item !== code)
    : [...current, code];
  emitPlatformChange(key);
}

function clearGroup(key: string) {
  currentValues()[key] = [];
  emitPlatformChange(key);
}

function confirm() {
  emit("confirm", Object.fromEntries(
    Object.entries(values.value).map(([key, list]) => [key, [...list]]),
  ));
}

function confirmParallel() {
  const copied = {
    boss: Object.fromEntries(
      Object.entries(parallelValues.value.boss || {}).map(([key, list]) => [key, [...list]]),
    ),
    zhilian: Object.fromEntries(
      Object.entries(parallelValues.value.zhilian || {}).map(([key, list]) => [key, [...list]]),
    ),
  } as Record<Platform, Record<string, string[]>>;
  emit("parallel-confirm", copied);
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

    <div
      v-if="isParallel"
      class="one-click-platform-tabs"
      data-testid="one-click-platform-tabs"
      role="tablist"
    >
      <button
        type="button"
        class="one-click-platform-tab"
        :class="{ active: isUnifiedPage }"
        data-testid="one-click-platform-tab-all"
        :aria-selected="isUnifiedPage"
        role="tab"
        @click="activeParallelPage = 'unified'"
      >全部</button>
      <button
        v-for="item in parallelPlatforms"
        :key="item"
        type="button"
        class="one-click-platform-tab"
        :class="{ active: activeParallelPlatform === item && !isUnifiedPage }"
        :data-testid="`one-click-platform-tab-${item}`"
        :aria-selected="activeParallelPlatform === item && !isUnifiedPage"
        role="tab"
        @click="activeParallelPage = item"
      >
        <span>{{ platformLabel(item) }}</span>
      </button>
    </div>

    <p v-if="isParallel" class="one-click-parallel-hint" data-testid="one-click-parallel-hint" role="status">
      全部平台将按当前条件一次启动，各平台可继续单独微调。
    </p>
    <p v-if="isParallel && errorMessage" class="one-click-mapping-error" data-testid="one-click-mapping-error" role="alert">
      {{ errorMessage }}
    </p>

    <div
      v-if="isParallel && isUnifiedPage"
      class="one-click-filter-groups"
      data-testid="one-click-unified-fields"
    >
      <fieldset
        v-for="[key, group] in unifiedGroupEntries"
        :key="key"
        class="filter-group one-click-filter-group"
      >
        <legend>{{ group.label }}</legend>
        <div class="chip-grid compact">
          <button
            class="choice-chip"
            :class="{ selected: !(props.unifiedValues?.[key] || []).length }"
            type="button"
            :aria-pressed="!(props.unifiedValues?.[key] || []).length"
            @click="emit('unified-change', key, [])"
          >不限</button>
          <button
            type="button"
            v-for="([label, code]) in group.options.filter(([label]) => label !== '不限')"
            :key="code"
            class="choice-chip"
            :class="{ selected: (props.unifiedValues?.[key] || []).includes(label) }"
            :aria-pressed="(props.unifiedValues?.[key] || []).includes(label)"
            @click="emit('unified-change', key, (props.unifiedValues?.[key] || []).includes(label)
              ? (props.unifiedValues?.[key] || []).filter((item) => item !== label)
              : [...(props.unifiedValues?.[key] || []), label])"
          >{{ label }}</button>
        </div>
      </fieldset>
    </div>

    <div v-else class="one-click-filter-groups">
      <fieldset
        v-for="group in visibleGroups"
        :key="group.key"
        class="filter-group one-click-filter-group"
      >
        <legend>{{ group.label }}</legend>
        <div class="chip-grid compact">
          <button
            v-if="group.sentinel"
            class="choice-chip"
            :class="{ selected: !(visibleValues[group.key] || []).length }"
            type="button"
            :aria-pressed="!(visibleValues[group.key] || []).length"
            @click="clearGroup(group.key)"
          >{{ group.sentinel.label }}</button>
          <button
            v-for="([label, code]) in group.options"
            :key="code"
            class="choice-chip"
            :class="{ selected: (visibleValues[group.key] || []).includes(code) }"
            type="button"
            :aria-pressed="(visibleValues[group.key] || []).includes(code)"
            @click="toggle(group.key, code)"
          >{{ label }}</button>
        </div>
      </fieldset>
      <p v-if="!visibleGroups.length" class="one-click-filter-empty">
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
        :disabled="isParallel && (confirmDisabled || preparing || loading)"
        @click="isParallel ? confirmParallel() : confirm()"
      >
        <Sparkles :size="17" aria-hidden="true" />{{ isParallel ? "开始全部平台筛选" : "开始筛选并 AI 优化" }}
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
.one-click-platform-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}
.one-click-platform-tab {
  border: 1px solid var(--hair);
  border-radius: 8px;
  padding: 7px 12px;
  color: var(--muted);
  background: var(--paper);
  cursor: pointer;
}
.one-click-platform-tab.active {
  color: var(--ink-1);
  border-color: var(--accent);
  background: var(--accent-wash);
}
.one-click-platform-tab-status {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: var(--muted);
}
.one-click-parallel-hint {
  margin: 0 0 14px;
  padding: 9px 12px;
  border: 1px solid var(--unsure-edge, var(--hair));
  border-radius: 8px;
  color: var(--unsure-deep);
  background: var(--unsure-wash);
  font-size: 13px;
  line-height: 1.5;
}
.one-click-platform-confirm {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  font-size: 13px;
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

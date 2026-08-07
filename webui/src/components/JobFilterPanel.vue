<script setup lang="ts">
// 筛选浮层面板：按 FILTER_GROUPS 渲染选项（choice-chip 风格），
// v-model 绑定草稿状态（确定/重置由父级 JobListToolbar 处理）。
// sentinel（不限）不存状态：选中态 = 该组数组为空；点非 sentinel 自动取消 sentinel（互斥）。
import { FILTER_GROUPS } from "../listFilter";
import type { FilterGroupDef, FilterState } from "../listFilter";

const props = defineProps<{
  modelValue: FilterState;
}>();

const emit = defineEmits<{
  "update:modelValue": [state: FilterState];
  apply: [];
  reset: [];
}>();

function groupValues(group: FilterGroupDef): string[] {
  return props.modelValue[group.key] as string[];
}

function isSelected(group: FilterGroupDef, value: string): boolean {
  return groupValues(group).includes(value);
}

function toggleOption(group: FilterGroupDef, value: string) {
  const values = groupValues(group);
  const next = values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
  emit("update:modelValue", { ...props.modelValue, [group.key]: next });
}

function selectSentinel(group: FilterGroupDef) {
  emit("update:modelValue", { ...props.modelValue, [group.key]: [] });
}
</script>

<template>
  <div class="popover-groups">
    <fieldset v-for="group in FILTER_GROUPS" :key="group.key" class="popover-group">
      <legend class="popover-group-label">{{ group.label }}</legend>
      <div class="chip-grid compact">
        <button
          v-if="group.sentinel"
          type="button"
          class="choice-chip"
          :class="{ selected: groupValues(group).length === 0 }"
          :aria-pressed="groupValues(group).length === 0"
          data-testid="filter-sentinel"
          @click="selectSentinel(group)"
        >{{ group.sentinel }}</button>
        <button
          v-for="option in group.options"
          :key="option.value"
          type="button"
          class="choice-chip"
          :class="{ selected: isSelected(group, option.value) }"
          :aria-pressed="isSelected(group, option.value)"
          data-testid="filter-option"
          @click="toggleOption(group, option.value)"
        >{{ option.label }}</button>
      </div>
      <p v-if="group.note" class="popover-group-note">{{ group.note }}</p>
    </fieldset>
  </div>
  <footer class="popover-footer">
    <button type="button" class="button secondary small" data-testid="filter-reset" @click="emit('reset')">重置</button>
    <button type="button" class="button primary small" data-testid="filter-apply" @click="emit('apply')">确定</button>
  </footer>
</template>
<script setup lang="ts">
import { computed } from "vue";
import type { ExecutionSelection, TaskSize } from "../types";

const props = withDefaults(defineProps<{
  modelValue: ExecutionSelection;
  taskSize: TaskSize;
  disabled?: boolean;
  busy?: boolean;
}>(), {
  disabled: false,
  busy: false,
});

const emit = defineEmits<{
  "update:modelValue": [selection: ExecutionSelection];
}>();

const options: Array<{ value: ExecutionSelection; label: string }> = [
  { value: "stable", label: "稳定" },
  { value: "balanced", label: "平衡" },
  { value: "extreme", label: "极限" },
  { value: "custom", label: "自定义" },
];
const sizeLabel = computed(() => ({
  small: "小任务",
  medium: "中任务",
  large: "大任务",
})[props.taskSize]);

function select(value: ExecutionSelection) {
  if (props.disabled || props.busy || value === props.modelValue) return;
  emit("update:modelValue", value);
}
</script>

<template>
  <div class="execution-mode-control">
    <div class="mode-context">
      <span>执行模式</span>
      <strong data-testid="mode-task-size">{{ sizeLabel }}</strong>
    </div>
    <div class="mode-segments" role="radiogroup" aria-label="执行模式">
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        role="radio"
        :data-mode="option.value"
        :aria-checked="modelValue === option.value"
        :disabled="disabled || busy"
        :class="{ active: modelValue === option.value }"
        @click="select(option.value)"
      >{{ option.label }}</button>
    </div>
  </div>
</template>

<style scoped>
.execution-mode-control {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.mode-context {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--muted, #64748b);
  font-size: 13px;
}

.mode-context strong {
  color: var(--text, #172033);
  font-size: 13px;
}

.mode-segments {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--line, #d9e0e8);
  border-radius: 7px;
  background: #f4f7f9;
}

.mode-segments button {
  min-width: 0;
  min-height: 38px;
  padding: 7px 8px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: #526071;
  font: inherit;
  font-weight: 650;
  cursor: pointer;
}

.mode-segments button.active {
  border-color: #bcc8d5;
  background: #fff;
  color: #172033;
  box-shadow: 0 1px 2px rgb(15 23 42 / 8%);
}

.mode-segments button:focus-visible {
  outline: 3px solid rgb(14 116 144 / 28%);
  outline-offset: 1px;
}

.mode-segments button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

@media (max-width: 430px) {
  .mode-segments {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>

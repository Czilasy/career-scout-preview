<script setup lang="ts">
import type { ExecutionSelection } from "../types";

const props = withDefaults(defineProps<{
  modelValue: ExecutionSelection;
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

function select(value: ExecutionSelection) {
  if (props.disabled || props.busy || value === props.modelValue) return;
  emit("update:modelValue", value);
}
</script>

<template>
  <div class="execution-mode-control">
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



.mode-segments {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-2);
}

.mode-segments button {
  min-width: 0;
  min-height: 38px;
  padding: 7px 8px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-weight: 650;
  cursor: pointer;
}

.mode-segments button.active {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--accent-ink);
}

.mode-segments button:focus-visible {
  outline: 3px solid rgb(14 116 144 / 28%);
  outline-offset: 1px;
}

.mode-segments button:hover:not(:disabled) {
  background: rgb(14 116 144 / 10%);
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

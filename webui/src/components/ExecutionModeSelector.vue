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
        :class="{ active: modelValue === option.value, [`mode-${option.value}`]: modelValue === option.value }"
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
  border: 1px solid var(--hair);
  border-radius: 9px;
  background: var(--panel);
}

.mode-segments button {
  min-width: 0;
  min-height: 34px;
  padding: 7px 8px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--ink-3);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: border-color .15s ease, background .15s ease, color .15s ease;
}

.mode-segments button.active {
  border-color: var(--brand-strong);
  background: var(--brand-wash);
  color: var(--brand-ink);
}

/* 024：档位配色——稳定=绿、平衡=黄、极限=红；自定义保持默认 brand 色 */
.mode-segments button.active.mode-stable {
  border-color: #22c55e;
  background: color-mix(in srgb, #22c55e 18%, transparent);
  color: #16a34a;
}

.mode-segments button.active.mode-balanced {
  border-color: #eab308;
  background: color-mix(in srgb, #eab308 18%, transparent);
  color: #a16207;
}

.mode-segments button.active.mode-extreme {
  border-color: #ef4444;
  background: color-mix(in srgb, #ef4444 18%, transparent);
  color: #dc2626;
}

.mode-segments button:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--brand) 28%, transparent);
  outline-offset: 1px;
}

.mode-segments button:hover:not(:disabled) {
  border-color: var(--brand);
  color: var(--brand-ink);
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

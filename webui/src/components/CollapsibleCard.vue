<script setup lang="ts">
import { ChevronDown } from "@lucide/vue";

const props = defineProps<{
  title: string;
  modelValue: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
}>();

function toggle() {
  emit("update:modelValue", !props.modelValue);
}
</script>

<template>
  <div class="collapsible-card content-card" :class="{ open: modelValue }">
    <div class="collapsible-header-row">
      <button
        type="button"
        class="collapsible-header"
        :aria-expanded="modelValue"
        @click="toggle"
      >
        <span class="collapsible-prefix"><slot name="prefix" /></span>
        <span class="collapsible-title">{{ title }}</span>
        <span class="collapsible-header-extra">
          <slot name="summary" />
          <ChevronDown :size="18" class="collapsible-chevron" aria-hidden="true" />
        </span>
      </button>
      <slot name="actions" />
    </div>
    <div class="collapsible-body" :class="{ open: modelValue }">
      <div class="collapsible-inner">
        <div class="collapsible-content">
          <slot />
        </div>
      </div>
    </div>
  </div>
</template>

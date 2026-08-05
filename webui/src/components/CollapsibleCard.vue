<script setup lang="ts">
import { ChevronDown } from "@lucide/vue";

const props = defineProps<{
  title: string;
  modelValue: boolean;
  /** static 模式：常驻展开，卡头不可点击、不显示折叠箭头（如步骤 2 双栏卡）。 */
  static?: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
}>();

function toggle() {
  if (props.static) return;
  emit("update:modelValue", !props.modelValue);
}
</script>

<template>
  <div class="collapsible-card content-card" :class="{ open: modelValue || static }">
    <div class="collapsible-header-row">
      <component
        :is="static ? 'div' : 'button'"
        :type="static ? undefined : 'button'"
        class="collapsible-header"
        :class="{ 'is-static': static }"
        :aria-expanded="static ? undefined : modelValue"
        @click="toggle"
      >
        <span class="collapsible-prefix"><slot name="prefix" /></span>
        <span class="collapsible-title">{{ title }}</span>
        <span class="collapsible-header-extra">
          <slot name="summary" />
          <ChevronDown v-if="!static" :size="18" class="collapsible-chevron" aria-hidden="true" />
        </span>
      </component>
      <slot name="actions" />
    </div>
    <div class="collapsible-body" :class="{ open: modelValue || static }">
      <div class="collapsible-inner">
        <div class="collapsible-content">
          <slot />
        </div>
      </div>
    </div>
  </div>
</template>

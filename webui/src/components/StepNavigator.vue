<script setup lang="ts">
import { computed } from "vue";
import { Check } from "@lucide/vue";

interface Step {
  id: string;
  label: string;
}

const props = defineProps<{
  steps: Step[];
  activeStep: string;
  enabledSteps: string[];
  completedSteps?: string[];
}>();

const emit = defineEmits<{ select: [step: string] }>();
const enabled = computed(() => new Set(props.enabledSteps));
const completed = computed(() => new Set(props.completedSteps || []));

function select(step: string) {
  if (enabled.value.has(step)) emit("select", step);
}
</script>

<template>
  <nav class="step-nav" aria-label="智能选岗进度">
    <ol>
      <li v-for="(step, index) in steps" :key="step.id">
        <button
          type="button"
          :disabled="!enabled.has(step.id)"
          :aria-current="activeStep === step.id ? 'step' : undefined"
          :class="{ active: activeStep === step.id, complete: completed.has(step.id) }"
          @click="select(step.id)"
        >
          <span class="step-index" aria-hidden="true">
            <Check v-if="completed.has(step.id)" :size="15" :stroke-width="2.4" />
            <template v-else>{{ index + 1 }}</template>
          </span>
          <span>{{ step.label }}</span>
        </button>
      </li>
    </ol>
  </nav>
</template>

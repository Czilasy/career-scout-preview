<script setup lang="ts">
import { X } from "@lucide/vue";
import JobLifecycleActions from "./JobLifecycleActions.vue";
import type { JobItem } from "../types";

defineProps<{
  open: boolean;
  profileId: string;
  job: JobItem | null;
}>();

const emit = defineEmits<{
  close: [];
  "job-feedback-changed": [payload: { profileId: string; jobId: string }];
}>();
</script>

<template>
  <!-- 岗位轨迹浮窗：居中弹窗，内容为原生命周期卡片全部能力 -->
  <Transition name="dialog">
    <div
      v-if="open && job"
      class="dialog-backdrop lifecycle-dialog-backdrop"
      data-testid="lifecycle-dialog"
      @click.self="emit('close')"
    >
      <section class="dialog-panel lifecycle-dialog" role="dialog" aria-modal="true" aria-label="岗位轨迹">
        <header class="lifecycle-dialog-header">
          <h2>岗位轨迹</h2>
          <button
            class="icon-button"
            type="button"
            aria-label="关闭岗位轨迹浮窗"
            data-testid="lifecycle-dialog-close"
            @click="emit('close')"
          >
            <X :size="18" aria-hidden="true" />
          </button>
        </header>
        <JobLifecycleActions
          :profile-id="profileId"
          :job="job"
          @job-feedback-changed="emit('job-feedback-changed', $event)"
        />
      </section>
    </div>
  </Transition>
</template>

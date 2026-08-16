<script setup lang="ts">
/**
 * 应用内更新弹窗（quitAndInstall 模式）。
 *
 * 状态机：info（更新说明）→ downloading（进度条）→ verifying →
 * ready（立即重启完成更新）；失败显示错误并提供重试/手动下载出口。
 * installable=false（源码模式）时只显示说明 + Release 页链接。
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { Download, LoaderCircle, Rocket } from "@lucide/vue";
import BaseDialog from "./BaseDialog.vue";
import {
  openExternalLink,
  quitDesktopApp,
  updateApi,
  userFacingMessage,
  type UpdateCheckResult,
  type UpdateProgress,
} from "../api";

const props = defineProps<{
  open: boolean;
  info: UpdateCheckResult | null;
}>();

const emit = defineEmits<{ close: []; ignore: [] }>();

const progress = ref<UpdateProgress | null>(null);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const restarting = ref(false);
let pollTimer: ReturnType<typeof setInterval> | null = null;

const phase = computed(() => progress.value?.status || "idle");
const percent = computed(() =>
  Math.min(100, Math.round((progress.value?.progress || 0) * 100)),
);
const checkedAtText = computed(() => {
  const at = props.info?.checked_at;
  if (!at) return "";
  const date = new Date(at * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
});
const sizeText = computed(() => {
  const size = props.info?.asset_size || 0;
  return size > 0 ? `${(size / 1024 / 1024).toFixed(1)} MB` : "";
});

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollOnce() {
  try {
    progress.value = await updateApi.status();
  } catch {
    // 轮询失败不打扰；下一拍重试
  }
  const status = progress.value?.status;
  if (status === "ready" || status === "failed") {
    if (status === "failed") error.value = failureMessage(progress.value?.error);
    stopPolling();
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollOnce, 600);
}

async function startDownload() {
  error.value = "";
  busy.value = true;
  try {
    const result = await updateApi.download();
    progress.value = result;
    if (result.status === "downloading" || result.status === "verifying") {
      startPolling();
    } else if (result.status === "ready") {
      stopPolling();
    }
  } catch (err) {
    error.value = userFacingMessage(err, "下载失败，请检查网络或磁盘空间后重试");
  } finally {
    busy.value = false;
  }
}

async function restartAndUpdate() {
  error.value = "";
  notice.value = "";
  restarting.value = true;
  try {
    await updateApi.restart();
    // 后端已启动替换脚本；优雅退出应用，脚本会替换文件并拉起新版本
    const quit = await quitDesktopApp();
    if (!quit) {
      // 退出未确认（极端情况）：脚本仍在等待主进程退出，手动关闭窗口后
      // 会自动完成替换并重启；保持按钮禁用，避免重复触发。
      notice.value = "未能自动退出，请手动关闭软件窗口；更新将在此后自动完成。";
    }
  } catch (err) {
    error.value = userFacingMessage(err, "重启更新失败，请关闭软件后手动下载更新");
    restarting.value = false;
  }
}

function failureMessage(code: string | undefined): string {
  switch (code) {
    case "sha256_mismatch": return "更新包校验失败（SHA256 不匹配），已丢弃，请重试下载。";
    case "sha256_unavailable": return "无法获取校验文件，为安全起见已中止，请到 Release 页手动下载。";
    case "invalid_download_url": return "下载地址不合法，请到 Release 页手动下载。";
    case "download_failed": return "下载失败，请检查网络或磁盘空间后重试；仍失败请到 Release 页手动下载。";
    case "updater_launch_failed": return "更新脚本启动失败，请关闭软件后到 Release 页手动下载更新。";
    case "download_start_failed": return "下载启动失败，请稍后重试；仍失败请到 Release 页手动下载。";
    default: return "更新失败，请重试；若仍失败请到 Release 页手动下载。";
  }
}

function retry() {
  progress.value = null;
  error.value = "";
  void startDownload();
}

watch(() => props.open, (open) => {
  if (!open) {
    stopPolling();
    error.value = "";
    notice.value = "";
    restarting.value = false;
    return;
  }
  // 打开时先看一眼后端状态（上次可能已下载完成）
  void pollOnce().then(() => {
    const status = progress.value?.status;
    if (status === "downloading" || status === "verifying") startPolling();
  });
});

onBeforeUnmount(stopPolling);
</script>

<template>
  <BaseDialog
    :open="open"
    :title="`发现新版本 v${info?.latest || ''}`"
    :description="`当前版本 v${info?.current || ''}`"
    size="md"
    @close="emit('close')"
  >
    <div class="update-dialog" data-testid="update-dialog">
      <p v-if="checkedAtText" class="update-checked-at" data-testid="update-checked-at">
        上次检查：{{ checkedAtText }}
      </p>
      <pre v-if="info?.release_notes" class="update-notes">{{ info.release_notes }}</pre>

      <div v-if="error" class="update-error" role="alert" data-testid="update-error">{{ error }}</div>
      <p v-if="notice" class="update-notice" data-testid="update-notice">{{ notice }}</p>

      <template v-if="info?.installable">
        <div v-if="phase === 'downloading' || phase === 'verifying'" class="update-progress">
          <div class="update-progress-bar" role="progressbar"
            :aria-valuenow="percent" aria-valuemin="0" aria-valuemax="100">
            <span :style="{ width: `${phase === 'verifying' ? 100 : percent}%` }"></span>
          </div>
          <p class="update-progress-text" data-testid="update-progress-text">
            {{ phase === 'verifying'
              ? '下载完成，正在校验文件…'
              : `正在下载 ${percent}%${sizeText ? `（共 ${sizeText}）` : ''}` }}
          </p>
        </div>

        <div v-else-if="phase === 'ready'" class="update-ready">
          <p>新版本已下载并通过完整性校验，重启后自动生效。</p>
        </div>
      </template>
      <template v-else>
        <p class="update-manual-hint">当前为源码运行模式，不支持应用内安装，请到 Release 页手动下载新版本。</p>
      </template>
    </div>

    <template #footer>
      <button class="button secondary" type="button" data-testid="update-ignore" @click="emit('ignore')">
        忽略此版本
      </button>
      <button class="button secondary" type="button" @click="emit('close')">稍后再说</button>
      <button
        v-if="info?.installable && phase === 'ready'"
        class="button primary"
        type="button"
        data-testid="update-restart"
        :disabled="restarting"
        @click="restartAndUpdate"
      >
        <LoaderCircle v-if="restarting" class="spin" :size="16" aria-hidden="true" />
        <Rocket v-else :size="16" aria-hidden="true" />
        {{ restarting ? "正在退出…" : "立即重启完成更新" }}
      </button>
      <button
        v-else-if="info?.installable && phase !== 'downloading' && phase !== 'verifying'"
        class="button primary"
        type="button"
        data-testid="update-download"
        :disabled="busy"
        @click="retry"
      >
        <LoaderCircle v-if="busy" class="spin" :size="16" aria-hidden="true" />
        <Download v-else :size="16" aria-hidden="true" />
        {{ phase === 'failed' ? '重试下载' : '下载并安装' }}
      </button>
      <button
        v-else-if="!info?.installable"
        class="button primary"
        type="button"
        @click="openExternalLink(info?.release_url || '')"
      >
        打开 Release 下载页
      </button>
    </template>
  </BaseDialog>
</template>

<style scoped>
.update-dialog {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.update-checked-at {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}
.update-notes {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: 12px;
  border-radius: 8px;
  background: var(--surface-2, rgba(127, 127, 127, 0.08));
  font-family: inherit;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.6;
}
.update-error {
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(220, 80, 60, 0.12);
  color: var(--danger, #d64541);
  font-size: 13px;
}
.update-notice {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}
.update-progress-bar {
  height: 10px;
  border-radius: 999px;
  overflow: hidden;
  background: rgba(127, 127, 127, 0.18);
}
.update-progress-bar span {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--accent, #4f8cff);
  transition: width 0.3s ease;
}
.update-progress-text {
  margin: 6px 0 0;
  font-size: 13px;
  opacity: 0.85;
}
.update-ready p,
.update-manual-hint {
  margin: 0;
  font-size: 14px;
}
</style>

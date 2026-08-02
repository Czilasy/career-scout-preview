<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Check, ExternalLink, Plus, Trash2, UserRound } from "@lucide/vue";
import BaseDialog from "./BaseDialog.vue";
import { apiRequest, errorMessage } from "../api";
import type { Notice } from "../types";

interface BrowserAccount {
  id: string;
  name: string;
  profile_dir: string;
  builtin?: boolean;
}

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: [] }>();

const accounts = ref<BrowserAccount[]>([]);
const activeAccount = ref("");
const busy = ref(false);
const busyAccount = ref("");
const serverBusy = ref(false);
const busyKind = ref("");
const lockedAccount = ref("");
const newName = ref("");
const localNotice = ref<Notice | null>(null);

watch(() => props.open, (open) => {
  if (open) {
    localNotice.value = null;
    newName.value = "";
    busyAccount.value = "";
    serverBusy.value = false;
    busyKind.value = "";
    lockedAccount.value = "";
    void loadAccounts();
  } else {
    localNotice.value = null;
    busyAccount.value = "";
  }
});

function setLocalNotice(notice: Notice) {
  localNotice.value = notice;
}

async function loadAccounts() {
  busy.value = true;
  try {
    const data = await apiRequest<{
      accounts?: BrowserAccount[];
      active_account?: string;
      busy?: boolean;
      busy_kind?: string;
      locked_account?: string;
    }>("/api/browser-accounts");
    accounts.value = data.accounts || [];
    activeAccount.value = data.active_account || "";
    serverBusy.value = Boolean(data.busy);
    busyKind.value = data.busy_kind || "";
    lockedAccount.value = data.locked_account || "";
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "浏览器账号加载失败"), tone: "error" });
  } finally {
    busy.value = false;
  }
}

async function addAccount() {
  const name = newName.value.trim();
  if (!name) return;
  busy.value = true;
  try {
    await apiRequest("/api/browser-accounts", {
      method: "POST",
      json: { name },
    });
    newName.value = "";
    setLocalNotice({ message: "浏览器账号已添加，可打开浏览器登录", tone: "success" });
    await loadAccounts();
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "添加浏览器账号失败"), tone: "error" });
  } finally {
    busy.value = false;
  }
}

async function activateAccount(id: string) {
  busyAccount.value = id;
  try {
    await apiRequest(`/api/browser-accounts/${encodeURIComponent(id)}/activate`, {
      method: "POST",
    });
    activeAccount.value = id;
    setLocalNotice({ message: "已设为当前账号，下一次任务将使用它", tone: "success" });
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "切换当前账号失败"), tone: "error" });
  } finally {
    busyAccount.value = "";
  }
}

async function openBrowser(id: string) {
  busyAccount.value = id;
  try {
    const data = await apiRequest<{ message?: string }>(
      `/api/browser-accounts/${encodeURIComponent(id)}/open`,
      { method: "POST" },
    );
    setLocalNotice({ message: data.message || "已打开自动化浏览器", tone: "info" });
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "打开浏览器失败"), tone: "error" });
  } finally {
    busyAccount.value = "";
  }
}

const lockNotice = computed(() => {
  if (!serverBusy.value) return "";
  if (busyKind.value === "paused" && lockedAccount.value) {
    const name = accounts.value.find((item) => item.id === lockedAccount.value)?.name || lockedAccount.value;
    return `当前有暂停任务，浏览器已锁定到「${name}」；请打开该账号登录/处理，或先结束/取消暂停任务。`;
  }
  return "当前有任务运行或暂停，请先结束或取消任务后再打开浏览器。";
});

function canOpen(id: string): boolean {
  if (busyAccount.value) return false;
  if (!serverBusy.value) return true;
  return busyKind.value === "paused" && lockedAccount.value === id;
}

async function removeAccount(id: string) {
  const account = accounts.value.find((item) => item.id === id);
  if (!account) return;
  if (!window.confirm(`删除「${account.name}」？该账号的自动化浏览器资料不会被删除，但将无法再选择。`)) return;
  busyAccount.value = id;
  try {
    await apiRequest(`/api/browser-accounts/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    if (activeAccount.value === id) activeAccount.value = "a";
    setLocalNotice({ message: "浏览器账号已删除", tone: "success" });
    await loadAccounts();
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "删除浏览器账号失败"), tone: "error" });
  } finally {
    busyAccount.value = "";
  }
}
</script>

<template>
  <BaseDialog
    id="browser-accounts"
    :open="open"
    title="自动化浏览器账号"
    description="提前为多个账号保存登录态；任务开始前在这里选择当前账号。"
    size="md"
    @close="$emit('close')"
  >
    <div class="browser-account-list">
      <div
        v-if="lockNotice"
        class="browser-account-notice"
        data-tone="warning"
        role="status"
      >
        {{ lockNotice }}
      </div>
      <article
        v-for="account in accounts"
        :key="account.id"
        class="browser-account-card"
        :data-active="account.id === activeAccount || undefined"
      >
        <div class="browser-account-head">
          <span class="browser-account-icon" aria-hidden="true"><UserRound :size="17" /></span>
          <strong>{{ account.name }}</strong>
          <span v-if="account.id === activeAccount" class="browser-account-badge">当前账号</span>
          <span v-else-if="account.builtin" class="browser-account-badge muted">内置</span>
        </div>
        <p class="browser-account-path" :title="account.profile_dir">{{ account.profile_dir }}</p>
        <div class="browser-account-actions">
          <button
            type="button"
            class="button secondary small"
            :disabled="!canOpen(account.id)"
            @click="openBrowser(account.id)"
          >
            <ExternalLink :size="15" aria-hidden="true" />打开浏览器登录
          </button>
          <button
            v-if="account.id !== activeAccount"
            type="button"
            class="button secondary small"
            :disabled="Boolean(busyAccount)"
            @click="activateAccount(account.id)"
          >
            <Check :size="15" aria-hidden="true" />设为当前账号
          </button>
          <button
            v-if="!account.builtin"
            type="button"
            class="button danger small"
            :disabled="Boolean(busyAccount)"
            @click="removeAccount(account.id)"
          >
            <Trash2 :size="15" aria-hidden="true" />删除
          </button>
        </div>
      </article>
      <p v-if="!accounts.length && !busy" class="browser-account-empty">暂无账号，先添加一个浏览器账号。</p>
    </div>

    <form class="browser-account-add" @submit.prevent="addAccount">
      <label class="field-label">
        <span>添加新浏览器账号</span>
        <input
          v-model.trim="newName"
          type="text"
          maxlength="30"
          required
          placeholder="例如：账号 C / 备用号"
        >
      </label>
      <button class="button primary" type="submit" :disabled="busy">
        <Plus :size="16" aria-hidden="true" />添加浏览器
      </button>
    </form>

    <div
      v-if="localNotice"
      class="browser-account-notice"
      :data-tone="localNotice.tone"
      role="status"
      aria-live="polite"
    >
      {{ localNotice.message }}
    </div>
  </BaseDialog>
</template>

<style scoped>
.browser-account-list {
  display: grid;
  gap: 10px;
  max-height: min(420px, 52vh);
  overflow-y: auto;
  margin-bottom: 18px;
}
.browser-account-card {
  display: grid;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-1);
}
.browser-account-card[data-active="true"] {
  border-color: color-mix(in srgb, var(--accent) 55%, var(--line));
}
.browser-account-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.browser-account-icon {
  display: inline-flex;
  width: 30px;
  height: 30px;
  align-items: center;
  justify-content: center;
  border-radius: 7px;
  color: var(--accent-ink);
  background: var(--accent);
}
.browser-account-badge {
  padding: 2px 8px;
  border-radius: 999px;
  color: var(--accent-ink);
  background: var(--accent);
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.browser-account-badge.muted {
  color: var(--muted);
  background: var(--surface-3);
}
.browser-account-path {
  margin: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.browser-account-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.browser-account-actions .button {
  min-height: 34px;
}
.browser-account-empty {
  margin: 0;
  color: var(--muted);
  text-align: center;
}
.browser-account-add {
  display: flex;
  align-items: end;
  gap: 10px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}
.browser-account-add .field-label {
  flex: 1 1 200px;
  min-width: 0;
  max-width: 320px;
}
.browser-account-add .button {
  flex: 0 0 auto;
  white-space: nowrap;
}
.browser-account-notice {
  margin-top: 14px;
  padding: 9px 11px;
  border-radius: 7px;
  color: var(--info);
  background: color-mix(in srgb, var(--info) 12%, transparent);
  font-size: 13px;
}
.browser-account-notice[data-tone="success"] {
  color: var(--success);
  background: color-mix(in srgb, var(--success) 12%, transparent);
}
.browser-account-notice[data-tone="error"] {
  color: var(--danger);
  background: color-mix(in srgb, var(--danger) 12%, transparent);
}
.browser-account-notice[data-tone="warning"] {
  color: var(--warning, #d97706);
  background: color-mix(in srgb, var(--warning, #d97706) 12%, transparent);
}
</style>

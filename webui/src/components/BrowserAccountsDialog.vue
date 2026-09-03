<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Check, ChevronDown, ExternalLink, Globe, LoaderCircle, Plus, Trash2, UserRound } from "@lucide/vue";
import BaseDialog from "./BaseDialog.vue";
import BrowserKernelPicker from "./BrowserKernelPicker.vue";
import { ApiError, apiRequest, browserRegistryApi, errorMessage } from "../api";
import type { BrowserRegistryEntryState, BrowserSelection } from "../api";
import type { BrowserAccount, Notice, Platform } from "../types";

// 平台展示标签；与后端 platform-schema display_name 解耦，前端只做稳定键→短标签映射。
const PLATFORM_LABELS: Record<Platform, string> = {
  boss: "BOSS",
  zhilian: "智联",
};

// 登录态缓存 TTL 与后端 scripts/login_state_cache.py 保持一致（15 分钟）。
const LOGIN_STATE_TTL_MS = 15 * 60 * 1000;

// D7：内置 ~/.career-scout/chrome-profile 账号在 UI 上固定叫「默认账号」，
// 是第一张卡片，不可删除/改名；未激活任何账号时任务回退到它。
function displayName(account: BrowserAccount): string {
  if (account.builtin && account.id === "a") return "默认账号";
  return account.name || account.id;
}

// 账号的可选登录平台；GET /api/browser-accounts 不再返回 profile_dir（http-api.md L319）。
function platformsOf(account: BrowserAccount): Platform[] {
  if (account.platforms) {
    const keys = Object.keys(account.platforms) as Platform[];
    if (keys.length) return keys;
  }
  // 旧响应回退：未带 platforms 时按双平台渲染。
  return ["boss", "zhilian"];
}

interface LoginStateRecord {
  state: "logged_in" | "not_logged_in" | "restricted" | "unknown";
  at: number;
}

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: [] }>();

const accounts = ref<BrowserAccount[]>([]);
const activeAccount = ref("");
const loginStates = ref<Record<string, Partial<Record<Platform, LoginStateRecord>>>>({});
const busy = ref(false);
const busyAccount = ref("");
const serverBusy = ref(false);
const busyKind = ref("");
const lockedAccount = ref("");
const lockedPlatform = ref("");
const newName = ref("");
const localNotice = ref<Notice | null>(null);
// 切换账号/打开窗口后缓存已失效或待重探的账号：徽章显示「待刷新」。
const pendingRefresh = ref<Set<string>>(new Set());
const pendingDelete = ref<BrowserAccount | null>(null);
// Spec 038 B091：账号池配置（多选 + 每账号配额）+ 撞墙限流视觉标识。
// 旧 R1/R2 角色互斥 schema 已弃用（FR-021），统一为 R1/R2 共用账号池。
const poolBusy = ref(false);
const POOL_R1_RANGE = { min: 1, max: 50, default: 25 } as const;
const POOL_R2_RANGE = { min: 1, max: 200, default: 100 } as const;

// 抓取浏览器（原独立「浏览器」设置合并至此）：清单/选择/生效路径随弹窗打开加载，
// chip 常显当前选择，展开后由 BrowserKernelPicker 选即存。
const kernelEntries = ref<BrowserRegistryEntryState[]>([]);
const kernelSelection = ref<BrowserSelection>({ mode: "auto" });
const kernelEffectivePath = ref<string | null>(null);
const kernelOpen = ref(false);

// 与账号操作同一把锁：任务运行中锁定，暂停时仍可切换。
const kernelLocked = computed(() => serverBusy.value && busyKind.value !== "paused");

const kernelChipLabel = computed(() => {
  const selection = kernelSelection.value;
  if (selection.mode === "registry" && selection.key) {
    const entry = kernelEntries.value.find((item) => item.key === selection.key);
    return entry?.name || selection.key;
  }
  if (selection.mode === "manual") return "手动指定";
  return "自动探测";
});

const kernelChipTitle = computed(() =>
  kernelEffectivePath.value
    ? `抓取浏览器：当前生效 ${kernelEffectivePath.value}`
    : "选择抓取使用的 Chromium 内核浏览器",
);

async function loadKernel() {
  try {
    const data = await browserRegistryApi.get();
    kernelEntries.value = data.registry || [];
    kernelSelection.value = data.selection || { mode: "auto" };
    kernelEffectivePath.value = data.effective_path || null;
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "抓取浏览器信息加载失败"), tone: "error" });
  }
}

function onKernelChanged(payload: { selection: BrowserSelection; effectivePath: string | null }) {
  kernelSelection.value = payload.selection;
  kernelEffectivePath.value = payload.effectivePath;
  kernelOpen.value = false;
  setLocalNotice({ message: "抓取浏览器已更新", tone: "success" });
}

function toggleKernel() {
  if (kernelLocked.value) return;
  kernelOpen.value = !kernelOpen.value;
}

// 抓取浏览器浮层：点浮层与 chip 之外的地方收起（不影响原有布局）。
const kernelPopEl = ref<HTMLElement | null>(null);
const kernelChipEl = ref<HTMLElement | null>(null);

function handleKernelPointerDown(event: PointerEvent) {
  if (!kernelOpen.value) return;
  const target = event.target as Node | null;
  if (!target) return;
  if (kernelPopEl.value?.contains(target)) return;
  if (kernelChipEl.value?.contains(target)) return;
  kernelOpen.value = false;
}
onMounted(() => {
  document.addEventListener("pointerdown", handleKernelPointerDown, true);
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", handleKernelPointerDown, true);
});

// Spec 038 B091：账号池配置（部分更新，复用 PUT /pool 端点）。
async function togglePoolSelected(account: BrowserAccount) {
  if (poolBusy.value) return;
  // account.pool 在后端投影里总是存在；防御性兜底用 true（默认进池）
  const currentSelected = account.pool?.selected ?? true;
  const next = !currentSelected;
  poolBusy.value = true;
  try {
    const order = next
      ? Math.max(
          -1,
          ...accounts.value.map((item) => Number(item.pool?.order ?? -1))
            .filter((value) => Number.isFinite(value)),
        ) + 1
      : undefined;
    await apiRequest(`/api/browser-accounts/${encodeURIComponent(account.id)}/pool`, {
      method: "PUT",
      json: { selected: next, ...(order === undefined ? {} : { order }) },
    });
    await loadAccounts();
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "账号池设置失败"), tone: "error" });
  } finally {
    poolBusy.value = false;
  }
}

async function updateQuota(account: BrowserAccount, field: "r1_quota" | "r2_quota", value: number) {
  if (poolBusy.value) return;
  poolBusy.value = true;
  try {
    await apiRequest(`/api/browser-accounts/${encodeURIComponent(account.id)}/pool`, {
      method: "PUT",
      json: { [field]: value },
    });
    await loadAccounts();
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "配额设置失败"), tone: "error" });
  } finally {
    poolBusy.value = false;
  }
}

watch(() => props.open, (open) => {
  if (open) {
    localNotice.value = null;
    newName.value = "";
    busyAccount.value = "";
    serverBusy.value = false;
    busyKind.value = "";
    lockedAccount.value = "";
    lockedPlatform.value = "";
    kernelOpen.value = false;
    void loadAccounts();
    void loadKernel();
  } else {
    localNotice.value = null;
    busyAccount.value = "";
    kernelOpen.value = false;
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
      login_states?: Record<string, Partial<Record<Platform, LoginStateRecord>>>;
      busy?: boolean;
      busy_kind?: string;
      locked_account?: string;
      locked_platform?: string;
    }>("/api/browser-accounts");
    accounts.value = data.accounts || [];
    activeAccount.value = data.active_account || "";
    loginStates.value = data.login_states || {};
    serverBusy.value = Boolean(data.busy);
    busyKind.value = data.busy_kind || "";
    lockedAccount.value = data.locked_account || "";
    lockedPlatform.value = data.locked_platform || "";
    // 响应里已有新鲜记录（15 分钟 TTL 内）的账号视为已重探，清除待刷新标记。
    const next = new Set<string>();
    for (const id of pendingRefresh.value) {
      const platformRecords = loginStates.value[id] || {};
      const fresh = Object.values(platformRecords).some(
        (record) => record && Date.now() - record.at * 1000 <= LOGIN_STATE_TTL_MS,
      );
      if (!fresh) next.add(id);
    }
    pendingRefresh.value = next;
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "账号加载失败"), tone: "error" });
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
    setLocalNotice({ message: "账号已添加，可分别打开平台浏览器登录", tone: "success" });
    await loadAccounts();
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "添加账号失败"), tone: "error" });
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
    // 切换后缓存可能还是旧账号的探测结果，标记待刷新直到真实重探发生。
    const next = new Set(pendingRefresh.value);
    next.add(id);
    pendingRefresh.value = next;
    setLocalNotice({ message: "已设为当前账号，继续暂停任务或新任务将使用它", tone: "success" });
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "切换当前账号失败"), tone: "error" });
  } finally {
    busyAccount.value = "";
  }
}

// 每个平台一个「打开」入口：只打开该账号指定平台的登录空间
// （各平台是独立 profile/端口，会各弹一个 Chrome 窗口）。
async function openPlatform(account: BrowserAccount, platform: Platform) {
  busyAccount.value = account.id;
  try {
    const data = await apiRequest<{ message?: string }>(
      `/api/browser-accounts/${encodeURIComponent(account.id)}/open`,
      { method: "POST", json: { platform } },
    );
    setLocalNotice({
      message: data?.message || `已打开 ${PLATFORM_LABELS[platform]} 的自动化浏览器，请登录`,
      tone: "info",
    });
    // 打开窗口会失效登录态缓存（后端 D3 信号），标记待刷新等真实探测。
    const next = new Set(pendingRefresh.value);
    next.add(account.id);
    pendingRefresh.value = next;
  } catch (error) {
    setLocalNotice({
      message: errorMessage(error, `打开${PLATFORM_LABELS[platform]}失败`),
      tone: "error",
    });
  } finally {
    busyAccount.value = "";
  }
  await loadAccounts();
}

// D7：徽章显示最后一次任务确认的结果；记录被软失效（打开窗口/续跑/重新登录
// 只回拨时间不删记录）或超过 15 分钟 TTL 时，结果保留并追加「待刷新」标注；
// 从未有过记录才是「未使用过」。
const STATE_BADGES: Record<string, { text: string; tone: string }> = {
  logged_in: { text: "已登录", tone: "ok" },
  not_logged_in: { text: "未登录", tone: "warn" },
  restricted: { text: "受限中", tone: "restricted" },
  unknown: { text: "状态未知", tone: "empty" },
};

function platformBadge(account: BrowserAccount, platform: Platform) {
  const record = loginStates.value[account.id]?.[platform];
  if (!record) {
    return { text: "未使用过", tone: "empty" };
  }
  const badge = STATE_BADGES[record.state];
  if (!badge) {
    return { text: "未使用过", tone: "empty" };
  }
  const stale = Date.now() - record.at * 1000 > LOGIN_STATE_TTL_MS;
  if (pendingRefresh.value.has(account.id) || stale) {
    return { text: `${badge.text} · 待刷新`, tone: "refresh" };
  }
  return badge;
}

// 锁定文案：暂停时中性提示，运行/排队保留「请先结束任务」提示。
const lockNotice = computed(() => {
  if (!serverBusy.value) return "";
  if (busyKind.value === "paused") {
    return "有暂停任务，可切换账号；切换后继续将使用新账号";
  }
  const name = lockedAccount.value
    ? accounts.value.find((item) => item.id === lockedAccount.value)?.name || lockedAccount.value
    : "";
  return name
    ? `有任务正在使用账号「${name}」，请先结束任务`
    : "当前有任务运行，请先结束或取消任务后再操作";
});

function canOpenPlatform(id: string, platform: Platform): boolean {
  if (busyAccount.value) return false;
  if (!serverBusy.value) return true;
  return busyKind.value === "paused";
}

function canManage(id: string): boolean {
  return !busyAccount.value && (!serverBusy.value || busyKind.value === "paused");
}

// 把 409 的双平台占用 details 拼成可读串（沿用既有契约）。
function formatDeleteError(error: unknown): string {
  const fallback = "删除账号失败";
  if (!(error instanceof ApiError)) {
    return errorMessage(error, fallback);
  }
  const base = String(error.payload.user_message || error.message || fallback);
  const details = (error.payload.details || {}) as Record<string, unknown>;
  const bits: string[] = [];
  const lockedPlatform = details.locked_platform;
  if (typeof lockedPlatform === "string" && lockedPlatform) {
    const label = PLATFORM_LABELS[lockedPlatform as Platform] || lockedPlatform;
    const runId = details.locked_run_id;
    bits.push(typeof runId === "string" && runId
      ? `${label} 被运行中任务 ${runId} 占用`
      : `${label} 被运行中任务占用`);
  }
  const conflictingPlatform = details.conflicting_platform;
  if (typeof conflictingPlatform === "string" && conflictingPlatform) {
    const label = PLATFORM_LABELS[conflictingPlatform as Platform] || conflictingPlatform;
    bits.push(`${label} 端口被未知 profile 占用`);
  }
  const busyPlatforms = details.busy_platforms;
  if (Array.isArray(busyPlatforms)) {
    for (const p of busyPlatforms) {
      if (typeof p === "string" && p) {
        const label = PLATFORM_LABELS[p as Platform] || p;
        if (!bits.some((b) => b.startsWith(label))) bits.push(`${label} 被占用`);
      }
    }
  }
  return bits.length ? `${base}（${bits.join("；")}）` : base;
}

// 删除账号：先打开应用内确认弹窗，确认后才执行删除（D5）。
function removeAccount(id: string) {
  const account = accounts.value.find((item) => item.id === id);
  if (!account) return;
  pendingDelete.value = account;
}

async function confirmRemoveAccount() {
  const account = pendingDelete.value;
  if (!account || busyAccount.value) return;
  pendingDelete.value = null;
  busyAccount.value = account.id;
  try {
    await apiRequest(`/api/browser-accounts/${encodeURIComponent(account.id)}`, {
      method: "DELETE",
    });
    if (activeAccount.value === account.id) activeAccount.value = "a";
    setLocalNotice({ message: "账号已删除", tone: "success" });
    await loadAccounts();
  } catch (error) {
    setLocalNotice({ message: formatDeleteError(error), tone: "error" });
  } finally {
    busyAccount.value = "";
  }
}
</script>

<template>
  <BaseDialog
    id="browser-accounts"
    :open="open"
    title="账号"
    description="每个账号使用独立的浏览器环境，BOSS 与智联窗口可分别打开；登录一次后长期有效，任务会使用「当前账号」的登录态抓取。多账号勾选进 R1/R2 共用账号池后，开抓会按勾选顺序轮询分摊（每轮每账号抓固定配额就换下一个，总量不够自动多轮覆盖），撞墙顺次切下一个预选账号继续。"
    size="md"
    @close="$emit('close')"
  >
    <div class="config-row" data-testid="config-row">
      <button
        ref="kernelChipEl"
        type="button"
        class="kernel-chip"
        data-testid="browser-kernel-chip"
        :aria-expanded="kernelOpen"
        :aria-haspopup="true"
        :title="kernelChipTitle"
        :disabled="kernelLocked"
        @click="toggleKernel"
      >
        <Globe :size="14" aria-hidden="true" />
        <span class="kernel-chip-label">抓取浏览器</span>
        <span class="kernel-chip-value">{{ kernelChipLabel }}</span>
        <ChevronDown :size="14" class="kernel-chip-caret" aria-hidden="true" />
      </button>
      <div
        v-if="kernelOpen"
        ref="kernelPopEl"
        class="kernel-popover"
        data-testid="browser-kernel-popover"
      >
        <BrowserKernelPicker
          :entries="kernelEntries"
          :selection="kernelSelection"
          :effective-path="kernelEffectivePath"
          :locked="kernelLocked"
          @changed="onKernelChanged"
        />
      </div>
    </div>

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
        :data-rate-limited="account.rate_limited ? 'true' : undefined"
      >
        <div class="browser-account-info">
          <div class="browser-account-head">
            <span class="browser-account-icon" aria-hidden="true"><UserRound :size="17" /></span>
            <strong :class="{ 'rate-limited-name': account.rate_limited }">{{ displayName(account) }}</strong>
            <span v-if="account.rate_limited" class="browser-account-badge rate-limited" :data-testid="`rate-limited-${account.id}`">限流</span>
            <span v-else-if="account.id === activeAccount" class="browser-account-badge">当前账号</span>
            <span v-else class="browser-account-badge muted">非当前账号</span>
          </div>
          <div class="browser-account-pool" :data-testid="`pool-config-${account.id}`">
            <label class="pool-toggle">
              <input
                type="checkbox"
                :checked="account.pool?.selected ?? true"
                :disabled="poolBusy"
                :data-testid="`pool-selected-${account.id}`"
                @change="togglePoolSelected(account)"
              >
              <span class="pool-toggle-label">参与轮询</span>
            </label>
            <label class="pool-quota">
              <span class="pool-quota-label">R1 配额</span>
              <input
                type="number"
                :min="POOL_R1_RANGE.min"
                :max="POOL_R1_RANGE.max"
                :value="account.pool?.r1_quota ?? POOL_R1_RANGE.default"
                :placeholder="`${POOL_R1_RANGE.min}-${POOL_R1_RANGE.max}`"
                :disabled="poolBusy"
                :data-testid="`pool-r1-quota-${account.id}`"
                @change="updateQuota(account, 'r1_quota', Number(($event.target as HTMLInputElement).value))"
              >
            </label>
            <label class="pool-quota">
              <span class="pool-quota-label">R2 配额</span>
              <input
                type="number"
                :min="POOL_R2_RANGE.min"
                :max="POOL_R2_RANGE.max"
                :value="account.pool?.r2_quota ?? POOL_R2_RANGE.default"
                :placeholder="`${POOL_R2_RANGE.min}-${POOL_R2_RANGE.max}`"
                :disabled="poolBusy"
                :data-testid="`pool-r2-quota-${account.id}`"
                @change="updateQuota(account, 'r2_quota', Number(($event.target as HTMLInputElement).value))"
              >
            </label>
          </div>
          <ul class="browser-account-platforms" :data-testid="`account-platforms-${account.id}`">
            <li
              v-for="platform in platformsOf(account)"
              :key="platform"
              :data-platform="platform"
            >
              <span class="browser-account-platform-label">{{ PLATFORM_LABELS[platform] }}</span>
              <span
                class="browser-account-state"
                :data-tone="platformBadge(account, platform).tone"
                :data-testid="`account-state-${account.id}-${platform}`"
              >
                {{ platformBadge(account, platform).text }}
              </span>
              <button
                type="button"
                class="browser-account-open"
                :data-testid="`open-${platform}-${account.id}`"
                :aria-label="`打开${PLATFORM_LABELS[platform]}浏览器`"
                :title="`打开${PLATFORM_LABELS[platform]}浏览器`"
                :disabled="!canOpenPlatform(account.id, platform)"
                @click="openPlatform(account, platform)"
              >
                <LoaderCircle v-if="busyAccount === account.id" class="spin" :size="13" aria-hidden="true" />
                <ExternalLink v-else :size="13" aria-hidden="true" />
                {{ busyAccount === account.id ? "打开中…" : "打开" }}
              </button>
            </li>
          </ul>
        </div>
        <div class="browser-account-actions">
          <div class="browser-account-icon-actions">
            <button
              v-if="account.id !== activeAccount"
              type="button"
              class="icon-button activate-toggle"
              :data-testid="`activate-${account.id}`"
              aria-label="设为当前账号"
              title="设为当前账号"
              :disabled="!canManage(account.id)"
              @click="activateAccount(account.id)"
            >
              <LoaderCircle v-if="busyAccount === account.id" class="spin" :size="17" aria-hidden="true" />
              <Check v-else :size="17" aria-hidden="true" />
            </button>
            <button
              v-if="!account.builtin"
              type="button"
              class="icon-button danger-icon"
              :data-testid="`delete-${account.id}`"
              aria-label="删除账号"
              title="删除账号"
              :disabled="!canManage(account.id)"
              @click="removeAccount(account.id)"
            >
              <Trash2 :size="17" aria-hidden="true" />
            </button>
          </div>
        </div>
      </article>
      <p v-if="!accounts.length && !busy" class="browser-account-empty">暂无账号，先添加一个账号。</p>
    </div>

    <form class="browser-account-add" @submit.prevent="addAccount">
      <label class="field-label">
        <span>添加新账号</span>
        <input
          v-model.trim="newName"
          type="text"
          maxlength="30"
          required
          placeholder="例如：账号 C / 备用号"
        >
      </label>
      <button class="button primary" type="submit" :disabled="busy">
        <LoaderCircle v-if="busy" class="spin" :size="16" aria-hidden="true" />
        <Plus v-else :size="16" aria-hidden="true" />
        {{ busy ? "添加中…" : "添加账号" }}
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

  <BaseDialog
    id="delete-account-confirm"
    :open="Boolean(pendingDelete)"
    title="删除账号"
    size="sm"
    @close="pendingDelete = null"
  >
    <p class="browser-account-confirm-text">
      删除「{{ pendingDelete ? displayName(pendingDelete) : '' }}」？该账号的自动化浏览器资料不会被删除，但将无法再选择。
    </p>
    <template #footer>
      <button
        type="button"
        class="button secondary"
        data-testid="delete-account-cancel"
        @click="pendingDelete = null"
      >取消</button>
      <button
        type="button"
        class="button danger"
        data-testid="delete-account-confirm"
        :disabled="Boolean(busyAccount)"
        @click="confirmRemoveAccount"
      >确认删除</button>
    </template>
  </BaseDialog>
</template>

<style scoped>
.config-row{position:relative;display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.kernel-chip{display:inline-flex;align-items:center;gap:6px;margin-left:auto;min-height:30px;padding:4px 10px;border:1px solid var(--hair);border-radius:7px;background:var(--panel-2);color:var(--ink-1);font:inherit;font-size:12px;font-weight:600;line-height:1.2;cursor:pointer;transition:border-color 160ms ease,background 160ms ease}
.kernel-chip:hover:not(:disabled),.kernel-chip[aria-expanded="true"]{border-color:var(--brand-edge);background:var(--brand-wash)}
.kernel-chip:disabled{cursor:not-allowed;opacity:.65}
.kernel-chip-label{color:var(--muted);font-weight:500}
.kernel-chip-value{color:var(--brand-strong);max-width:96px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kernel-popover{position:absolute;z-index:60;top:calc(100% + 6px);right:0;width:min(340px,calc(100% - 4px));max-height:min(460px,60vh);overflow-y:auto;padding:10px;border:1px solid var(--hair);border-radius:9px;background:var(--panel);box-shadow:0 14px 40px rgba(0,0,0,.18),0 2px 8px rgba(0,0,0,.08)}
.browser-account-list {
  display: grid;
  gap: 10px;
  max-height: min(420px, 52vh);
  overflow-y: auto;
  margin-bottom: 18px;
}
.browser-account-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--hair);
  border-radius: 10px;
  background: var(--panel);
}
.browser-account-card[data-rate-limited="true"] {
  border-color: var(--danger-edge, #f0a0a0);
  background: var(--danger-wash, rgba(220, 60, 60, 0.06));
}
.browser-account-info {
  display: grid;
  gap: 8px;
  flex: 1 1 auto;
  min-width: 0;
}
.browser-account-card[data-active="true"] {
  border-color: var(--brand-edge);
  background: var(--brand-wash);
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
  border-radius: 8px;
  color: var(--brand-strong);
  background: var(--brand-wash);
}
.browser-account-badge {
  padding: 2px 8px;
  border: 1px solid var(--brand-edge);
  border-radius: 999px;
  color: var(--brand-ink);
  background: var(--brand-wash);
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.browser-account-badge.muted {
  color: var(--ink-3);
  border-color: var(--hair);
  background: var(--hair-2);
}
/* Spec 038 B091 FR-014：撞墙限流账号标红 + "限流"方框 */
.browser-account-badge.rate-limited {
  color: #c01818;
  border-color: #e04040;
  background: rgba(220, 60, 60, 0.1);
}
.rate-limited-name {
  color: #c01818;
}
/* 账号池配置行：参与轮询开关 + R1/R2 配额输入 */
.browser-account-pool {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
}
.pool-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 12px;
  color: var(--ink-1);
}
.pool-toggle input[type="checkbox"] {
  margin: 0;
}
.pool-toggle-label {
  font-weight: 500;
}
.pool-quota {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ink-2);
}
.pool-quota-label {
  color: var(--muted);
}
.pool-quota input[type="number"] {
  width: 60px;
  padding: 3px 6px;
  border: 1px solid var(--hair);
  border-radius: 5px;
  background: var(--panel-2);
  color: var(--ink-1);
  font: inherit;
  font-size: 12px;
}
.pool-quota input[type="number"]:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.browser-account-platforms {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.browser-account-platforms li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.4;
}
.browser-account-open {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 26px;
  padding: 3px 9px;
  border: 1px solid var(--brand-edge);
  border-radius: 6px;
  color: var(--brand-strong);
  background: var(--panel);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.2;
  cursor: pointer;
}
.browser-account-open:hover:not(:disabled) {
  color: var(--brand-ink);
  background: var(--brand-wash);
}
.browser-account-open:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.browser-account-platform-label {
  font-weight: 600;
  color: var(--ink-1);
}
.browser-account-state {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}
.browser-account-state[data-tone="ok"] {
  color: var(--match-deep);
  background: var(--match-wash);
}
.browser-account-state[data-tone="warn"] {
  color: var(--unsure-deep);
  background: var(--unsure-wash);
}
.browser-account-state[data-tone="restricted"] {
  color: var(--reject-deep);
  background: var(--reject-wash);
}
.browser-account-state[data-tone="refresh"] {
  color: var(--unsure-deep);
  background: var(--unsure-wash);
  font-weight: 600;
}
.browser-account-state[data-tone="empty"] {
  color: var(--ink-3);
  background: var(--hair-2);
  font-weight: 600;
}
.browser-account-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
  flex: 0 0 auto;
  margin-left: auto;
}
.browser-account-icon-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.activate-toggle {
  color: var(--match-deep);
}
.danger-icon {
  color: var(--reject-deep);
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
  border-top: 1px solid var(--hair);
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
  color: var(--brand-ink);
  background: var(--brand-wash);
  font-size: 13px;
}
.browser-account-notice[data-tone="success"] {
  color: var(--match-deep);
  background: var(--match-wash);
}
.browser-account-notice[data-tone="error"] {
  color: var(--reject-deep);
  background: var(--reject-wash);
}
.browser-account-notice[data-tone="warning"] {
  color: var(--unsure-deep);
  background: var(--unsure-wash);
}
</style>

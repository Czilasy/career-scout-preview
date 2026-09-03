<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Bell, History, LoaderCircle, Moon, Settings, Star, Sun, X } from "@lucide/vue";
import AiSettingsDialog from "./components/AiSettingsDialog.vue";
import BrowserAccountsDialog from "./components/BrowserAccountsDialog.vue";
import EnvCheckDialog from "./components/EnvCheckDialog.vue";
import ReminderDrawer from "./components/ReminderDrawer.vue";
import UpdateDialog from "./components/UpdateDialog.vue";
import AppSettingsMenu from "./components/AppSettingsMenu.vue";
import WindowTitleBar from "./components/WindowTitleBar.vue";
import DynamicIsland, { type CapsuleTarget } from "./components/DynamicIsland.vue";
import { requestCapsuleNavigation, type CapsuleStatusPayload } from "./composables/useDiscoveryState";
import { createIslandNotices } from "./composables/useIslandNotices";
import { useIslandCarousel, type IslandInterruptContent, type IslandLane } from "./composables/useIslandCarousel";
import { useReminderBadge } from "./composables/useReminderBadge";
import DiscoveryView from "./views/DiscoveryView.vue";
import { apiRequest, currentRuntimeMode, errorMessage, GITHUB_REPO_URL, initializeSession, openExternalLink, updateApi, type UpdateCheckResult } from "./api";
import { safeCanonicalUrl } from "./jobFeedback";
import type { RoundStatusPayload } from "./discovery";
import { useTheme } from "./composables/useTheme";
import ThemePickerOptions from "./themes/ThemePickerOptions.vue";
import KaleidoField from "./themes/kaleido/KaleidoField.vue";
import { isThemeId } from "./themes/registry";
import { cleanJobLocation } from "./location";
import type { CandidateProfile, Notice } from "./types";

// 主题切换：mode（明暗）由顶栏按钮触发；platform（boss/智联）由 DiscoveryView 同步。
const { mode, toggleTheme } = useTheme();

const themeToggleLabel = computed(() =>
  mode.value === "light" ? "切换到暗色模式" : "切换到浅色模式");

// ---------------------------------------------------------------------------
// 顶栏本轮状态胶囊（036 灵动岛）：数据来自 DiscoveryView 上抛的 round-status，
// 空闲常驻显示平台名（FR-012）。点击经 requestCapsuleNavigation 派发导航。
// ---------------------------------------------------------------------------
const roundStatus = ref<CapsuleStatusPayload | null>(null);

function handleRoundStatus(payload: RoundStatusPayload | null) {
  roundStatus.value = payload as CapsuleStatusPayload | null;
}

// 037 复审 P2-8：岛 navigate 分流——"reminders"（投递提醒打断行）开提醒抽屉；
// requestCapsuleNavigation 不认识它（useDiscoveryState 禁改），分流在 App 层做。
function handleIslandNavigate(target: CapsuleTarget) {
  if (target === "reminders") {
    if (!reminderDrawerOpen.value) toggleReminderDrawer();
    return;
  }
  requestCapsuleNavigation(target);
}

// 页面标题随平台与页面状态变化；结果/双平台场景使用通用标题，不出现平台独占文案。
const pageTitle = computed(() => {
  if (roundStatus.value?.phase === "judged") return "Career Scout · 职位工作台";
  if (roundStatus.value?.phase === "scraping" || roundStatus.value?.phase === "screening") {
    const label = roundStatus.value.platform === "zhilian" ? "智联" : "BOSS";
    return `Career Scout · ${label}工作台`;
  }
  return "Career Scout 工作台";
});
watch(pageTitle, (title) => { document.title = title; }, { immediate: true });

const aiSettingsOpen = ref(false);
const browserAccountsOpen = ref(false);
const envCheckOpen = ref(false);
const settingsMenuOpen = ref(false);
const updateChecking = ref(false);
const discoveryRef = ref<{
  openHistoryDrawer: () => void;
  toggleHistoryDrawer: () => void;
  closeHistoryDrawer: () => void;
} | null>(null);

// ---------------------------------------------------------------------------
// 037 灵动岛 v3：通知池由胶囊状态跃迁派生（App 持有）；面板开闭由
// DynamicIsland 内部 open 状态控制；面板与三抽屉互斥（开任一即 collapse）。
// 037 灵动岛 v3：carousel 状态机接管打断轮转；onSinkInterrupt 把转完的打断
// 沉入 islandNotices 作未读条目（kind:"interrupt" + tone 染色），角标由
// DynamicIsland 组合 notices 未读 + badgeCount 显示。
// ---------------------------------------------------------------------------
const islandNotices = createIslandNotices(roundStatus);
const islandCarousel = useIslandCarousel(roundStatus, {
  onSinkInterrupt: (lane) => {
    // interrupt lane 的 content 是 IslandInterruptContent；窄化为 notice 字段。
    // target 按打断类型透传（缺省 task；投递提醒给 reminders 开提醒抽屉）。
    const c = lane.content as IslandInterruptContent;
    islandNotices.sinkInterrupt({
      id: lane.id,
      kind: "interrupt",
      title: c.title,
      detail: c.detail,
      tone: c.tone,
      target: c.target ?? "task",
    });
  },
});

// 037 边角（复审 P2-5）：scope=history（浏览历史轮）期间打断暂停消费——
// 先攒入缓冲，回到最新（scope 离开 history）时逐条 flush：最后一条占据展示位
// （"只转最新一条"），余数照常计时沉入 panel；不打断历史轮的浏览。
const historyPendingInterrupts: Omit<IslandLane, "id" | "type">[] = [];

function pushInterruptOrDefer(lane: Omit<IslandLane, "id" | "type">): void {
  if (roundStatus.value?.scope === "history") {
    historyPendingInterrupts.push(lane);
    return;
  }
  islandCarousel.pushInterrupt(lane);
}

watch(
  () => roundStatus.value?.scope,
  (scope) => {
    if (scope === "history" || historyPendingInterrupts.length === 0) return;
    const queued = historyPendingInterrupts.splice(0);
    for (const pending of queued) islandCarousel.pushInterrupt(pending);
  },
);
const islandRef = ref<{ collapse: () => void } | null>(null);
function collapseIsland() {
  islandRef.value?.collapse?.();
}

function toggleHistoryDrawer() {
  // 与收藏/提醒互斥：打开历史时收起另外两个抽屉。
  collapseIsland();
  favoritesOpen.value = false;
  reminderDrawerOpen.value = false;
  discoveryRef.value?.toggleHistoryDrawer?.();
}

function closeHistoryDrawer() {
  discoveryRef.value?.closeHistoryDrawer?.();
}

function openAiSettingsFromMenu() {
  settingsMenuOpen.value = false;
  aiSettingsOpen.value = true;
}

// 037 复审 B3：设置菜单与灵动岛/主题选择框互斥——打开时收起岛与主题框。
function toggleSettingsMenu() {
  if (settingsMenuOpen.value) {
    settingsMenuOpen.value = false;
    return;
  }
  collapseIsland();
  themePickerOpen.value = false;
  settingsMenuOpen.value = true;
}
function openBrowserAccountsFromMenu() {
  settingsMenuOpen.value = false;
  browserAccountsOpen.value = true;
}
function openEnvCheckFromMenu() {
  settingsMenuOpen.value = false;
  envCheckOpen.value = true;
}
function manualUpdateFromMenu() {
  settingsMenuOpen.value = false;
  void manualCheckUpdate();
}
const profiles = ref<CandidateProfile[]>([]);
const currentProfileId = ref("");

const favoritesOpen = ref(false);
const favorites = ref<Record<string, unknown>[]>([]);
const favoriteRemovingIds = ref(new Set<string>());
const favPanelEl = ref<HTMLElement | null>(null);
const favTriggerEl = ref<HTMLButtonElement | null>(null);

async function loadFavorites() {
  try {
    const data = await apiRequest<{ items?: Record<string, unknown>[] }>("/api/favorites");
    favorites.value = data.items || [];
  } catch {
    favorites.value = [];
  }
}

function favoriteMeta(job: Record<string, unknown>): string {
  return [job.salary || "薪资面议", cleanJobLocation(job.location)].filter(Boolean).join(" · ");
}

function favoriteJobUrl(job: Record<string, unknown>): string {
  const raw = String(job.job_link || job.canonical_url || "");
  const platform = job.platform === "zhilian" ? "zhilian" : "boss";
  return safeCanonicalUrl(platform, raw) || "#";
}

function toggleFavorites() {
  if (favoritesOpen.value) {
    favoritesOpen.value = false;
  } else {
    favoritesOpen.value = true;
    // 两个抽屉互斥：打开收藏时收起提醒，避免叠加导致点空白“回落到收藏”。
    collapseIsland();
    reminderDrawerOpen.value = false;
    closeHistoryDrawer();
    void loadFavorites();
  }
}

// 点击收藏面板与触发按钮之外的任意区域自动收起（与提醒抽屉的点外部关闭一致）。
function onDocPointerDown(event: PointerEvent) {
  if (!favoritesOpen.value) return;
  const target = event.target as Node | null;
  if (!target) return;
  if (favPanelEl.value && favPanelEl.value.contains(target)) return;
  if (favTriggerEl.value && favTriggerEl.value.contains(target)) return;
  favoritesOpen.value = false;
}

// 抽屉打开时聚焦面板并挂全局指针监听；关闭时移除监听（Esc 仍可关闭）。
watch(favoritesOpen, (open) => {
  if (open) {
    nextTick(() => favPanelEl.value?.focus());
    document.addEventListener("pointerdown", onDocPointerDown);
  } else {
    document.removeEventListener("pointerdown", onDocPointerDown);
  }
});

function handleFavoritesKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") favoritesOpen.value = false;
}

async function removeFavorite(job: Record<string, unknown>) {
  const profileId = String(job.profile_id || "");
  const jobId = String(job.job_id || "");
  if (!profileId || !jobId) return;
  const key = `${profileId}:${jobId}`;
  const next = new Set(favoriteRemovingIds.value);
  next.add(key);
  favoriteRemovingIds.value = next;
  try {
    await apiRequest("/api/pipeline/jobs/interest/cancel", {
      method: "POST",
      json: {
        profile_id: profileId,
        // 只传内部 job_id（favorites 列表的 job_id 就是 jobs.id）。
        // 不能附带 job_link 等身份候选字段：后端权威身份解析见
        // _pipeline_identity_payload —— 部分三元组 + 内部 ID 会被判定为
        // “身份信息不完整”而拒绝（此前取消收藏必现 422 报错）。
        job: { job_id: jobId },
      },
    });
    favorites.value = favorites.value.filter((j) => j.job_id !== job.job_id);
    showNotice({ message: "已取消收藏", tone: "info" });
  } catch (error) {
    showNotice({ message: errorMessage(error, "取消收藏失败"), tone: "error" });
  }
  finally {
    const after = new Set(favoriteRemovingIds.value);
    after.delete(key);
    favoriteRemovingIds.value = after;
  }
}

onMounted(async () => {
  try {
    document.title = pageTitle.value;
    await initializeSession();
    runtimeMode.value = currentRuntimeMode() === "exe" ? "exe" : "source";
    const profileData = await apiRequest<{ profiles?: CandidateProfile[] }>("/api/profiles");
    profiles.value = profileData.profiles || [];
    if (!profiles.value.length) {
      // 空库首启兜底：创建画像的入口全在 DiscoveryView 内部，而它只在
      // currentProfileId 就绪后才挂载——不在这里自动建首个画像，
      // 新装用户会永远停在「正在加载工作台…」。
      const created = await apiRequest<CandidateProfile>("/api/profiles", {
        method: "POST",
        json: { name: "我的求职画像", confirmed_fields: {} },
      });
      profiles.value = [created];
    }
    const saved = localStorage.getItem("career-scout-current-profile") || "";
    currentProfileId.value = profiles.value.some((profile) => profile.id === saved)
      ? saved
      : profiles.value[0]?.id || "";
    // 顶栏收藏徽标随首屏加载展示，不必等用户先打开过面板。
    void loadFavorites();
    // 检查更新：仅桌面版后台静默执行，失败不打扰（更新缓存已关闭，始终实时检查）
    if (updatesEnabled.value) void checkAppUpdate();
  } catch (error) {
    showNotice({ message: errorMessage(error, "WebUI 初始化失败"), tone: "error" });
  }
});

// ---------------------------------------------------------------------------
// 应用内更新：仅桌面 EXE 模式启用；源码模式不检查、不提示（GitHub 链接保留）。
const runtimeMode = ref<"source" | "exe">("source");
const updatesEnabled = computed(() => runtimeMode.value === "exe");
// ---------------------------------------------------------------------------
const updateInfo = ref<UpdateCheckResult | null>(null);
const updateDialogOpen = ref(false);
/** 用户点过「忽略此版本」的版本号（localStorage 持久化，跨启动生效）。 */
const IGNORED_UPDATE_KEY = "career-scout-ignored-update";

function ignoredUpdateVersion(): string {
  try {
    return window.localStorage.getItem(IGNORED_UPDATE_KEY) || "";
  } catch {
    return "";
  }
}

async function checkAppUpdate() {
  try {
    // 更新检查缓存已关闭：每次启动都会实时请求 GitHub，发布新版本后下次必弹。
    // 网络失败仍静默，更新提示不是关键路径。
    const result = await updateApi.check();
    if (result?.ok && result.has_update) {
      updateInfo.value = result;
      // 首次发现新版本自动弹出；用户忽略过该版本则只保留红点入口。
      if (result.latest !== ignoredUpdateVersion()) {
        updateDialogOpen.value = true;
      }
    }
  } catch {
    // 无网/限流/后端异常都静默，更新提示不是关键路径
  }
}

/** 「忽略此版本」：记住版本号，本次及后续启动不再自动弹窗（红点保留）。 */
function ignoreThisVersion() {
  const latest = updateInfo.value?.latest;
  if (!latest) return;
  try {
    window.localStorage.setItem(IGNORED_UPDATE_KEY, latest);
  } catch {
    /* 持久化失败不阻断关闭 */
  }
  updateDialogOpen.value = false;
}

// 手动检查更新：始终实时请求 GitHub latest release，无更新时给出明确反馈。
async function manualCheckUpdate() {
  updateChecking.value = true;
  try {
    const result = await updateApi.check();
    if (result?.ok && result.has_update) {
      updateInfo.value = result;
      updateDialogOpen.value = true;
    } else if (result?.ok && !result.has_update) {
      showNotice({ message: "已是最新版本", tone: "success" });
    } else {
      // result.ok=false：检查失败（限速/网络异常/后端错误），不再误报"已是最新"
      showNotice({ message: "检查更新失败，请稍后重试", tone: "warning" });
    }
  } catch {
    showNotice({ message: "检查更新失败，请检查网络后重试", tone: "warning" });
  }
  finally {
    updateChecking.value = false;
  }
}

function handleThemeToggle(event: MouseEvent) {
  if (themeLongPressFired) {
    // 长按蓄力结束后浏览器补发的 click，吞掉：避免选择框弹出的同时误切明暗。
    themeLongPressFired = false;
    return;
  }
  if (themePickerOpen.value) {
    // 选择框开着时再点按钮：只收起选择框，不切换明暗。
    themePickerOpen.value = false;
    return;
  }
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    toggleTheme();
    return;
  }
  const x = event.clientX;
  const y = event.clientY;
  toggleTheme();
  const ripple = document.createElement("span");
  ripple.className = "theme-ripple";
  ripple.style.setProperty("--ripple-x", `${x}px`);
  ripple.style.setProperty("--ripple-y", `${y}px`);
  ripple.setAttribute("aria-hidden", "true");
  document.body.appendChild(ripple);
  ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
}

// 长按弹层选择（032）：亮/暗直切；万花筒经 useTheme 值域扩展生效。
function handleThemePick(id: string) {
  themePickerOpen.value = false;
  if (!isThemeId(id) || id === mode.value) return;
  toggleTheme(id);
}

// ---- 主题长按蓄力（彩蛋主题入口；普通点击仍是明暗切换，互不干扰）----
// 视觉契约：图标小幅发抖且抖动幅度随进度增大、图标本身略微放大，图标线条与
// 按钮外框逐渐发亮；外框只发光不抖动。1 秒蓄满弹出选择框并整体还原（单向动画）。
const THEME_CHARGE_MS = 1000;
const themeCharging = ref(false);
const themePickerOpen = ref(false);
const themeToggleEl = ref<HTMLElement | null>(null);
const themePickerEl = ref<HTMLElement | null>(null);
let themeChargeTimer: number | null = null;
let themeChargeRaf: number | null = null;
let themeChargeStart = 0;
let themeLongPressFired = false;

function applyChargeVisual(now: number) {
  const btn = themeToggleEl.value;
  if (!btn) return;
  const t = Math.min(1, (now - themeChargeStart) / THEME_CHARGE_MS);
  // 抖动幅度随进度增大；系统声明"减少动态"时保留发光、去掉抖动与放大
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const amp = reduce ? 0 : 1.7 * t;
  btn.style.setProperty("--charge", t.toFixed(3));
  btn.style.setProperty("--charge-jx", `${(Math.sin(now / 26) * amp).toFixed(2)}px`);
  btn.style.setProperty("--charge-jy", `${(Math.cos(now / 21) * amp * 0.8).toFixed(2)}px`);
  btn.style.setProperty("--charge-scale", (reduce ? 1 : 1 + 0.12 * t).toFixed(3));
}

function clearChargeVisual() {
  const btn = themeToggleEl.value;
  if (!btn) return;
  for (const name of ["--charge", "--charge-jx", "--charge-jy", "--charge-scale"]) {
    btn.style.removeProperty(name);
  }
}

function stopChargeLoop() {
  if (themeChargeTimer !== null) {
    window.clearTimeout(themeChargeTimer);
    themeChargeTimer = null;
  }
  if (themeChargeRaf !== null && typeof window.cancelAnimationFrame === "function") {
    window.cancelAnimationFrame(themeChargeRaf);
    themeChargeRaf = null;
  }
}

function startThemeCharge(event: PointerEvent) {
  if (event.button !== 0) return; // 只响应主键，右键/触控笔附加键不蓄力
  themeLongPressFired = false;
  cancelThemeCharge();
  themeCharging.value = true;
  themeChargeStart = performance.now();
  const step = (now: number) => {
    if (!themeCharging.value) return;
    applyChargeVisual(now);
    themeChargeRaf = window.requestAnimationFrame(step);
  };
  if (typeof window.requestAnimationFrame === "function") {
    themeChargeRaf = window.requestAnimationFrame(step);
  }
  themeChargeTimer = window.setTimeout(() => {
    themeChargeTimer = null;
    finishThemeCharge();
  }, THEME_CHARGE_MS);
}

function finishThemeCharge() {
  stopChargeLoop();
  themeCharging.value = false;
  clearChargeVisual(); // 蓄满即还原：抖动停止、大小回原、光晕收回，随后弹框
  themeLongPressFired = true;
  // 037 复审 B3：主题选择框与灵动岛互斥，弹出前收起岛与设置菜单。
  collapseIsland();
  settingsMenuOpen.value = false;
  themePickerOpen.value = true;
}

function cancelThemeCharge() {
  stopChargeLoop();
  themeCharging.value = false;
  clearChargeVisual();
}

// 点击选择框与触发按钮之外的任意区域自动收起（与收藏抽屉的点外部关闭一致）。
function onThemePickerDocPointerDown(event: PointerEvent) {
  if (!themePickerOpen.value) return;
  const target = event.target as Node | null;
  if (!target) return;
  if (themePickerEl.value && themePickerEl.value.contains(target)) return;
  if (themeToggleEl.value && themeToggleEl.value.contains(target)) return;
  themePickerOpen.value = false;
}

watch(themePickerOpen, (open) => {
  if (open) {
    document.addEventListener("pointerdown", onThemePickerDocPointerDown);
  } else {
    document.removeEventListener("pointerdown", onThemePickerDocPointerDown);
  }
});

function openGitHub() {
  void openExternalLink(GITHUB_REPO_URL);
}

onBeforeUnmount(() => {
  // 037：清 carousel 打断队列的残留 timer，避免组件卸载后 fake/real timer
  // 回调在死队列上 mutate（虽无观察者，但属卫生）。
  islandCarousel.reset();
  document.removeEventListener("pointerdown", onDocPointerDown);
  cancelThemeCharge();
  document.removeEventListener("pointerdown", onThemePickerDocPointerDown);
});

function selectProfile(profileId: string) {
  currentProfileId.value = profileId;
  if (profileId) localStorage.setItem("career-scout-current-profile", profileId);
}

function showNotice(next: Notice) {
  // 037 复审补齐：所有信息提示融入灵动岛，不再有独立 notice toast 浮窗
  // （用户要求「信息性小条幅全部进灵动岛，不要弹出浮窗」，按钮自带即时
  // 反馈不在此列）。warning/error 保留原 tone；info/success 映射 warning
  // （琥珀提示色），打断展示 ~2.2s 后沉入 panel 未读；history 浏览期间
  // 经 pushInterruptOrDefer 顺延到回到最新（复审 P2-5）。
  const tone: "warning" | "error" =
    next.tone === "error" ? "error" : "warning";
  pushInterruptOrDefer({
    content: { title: next.message, detail: "", tone, target: "task" },
    duration: 2200,
  });
}

function acceptCreatedProfile(profile: CandidateProfile) {
  const index = profiles.value.findIndex((item) => item.id === profile.id);
  if (index >= 0) profiles.value[index] = profile;
  else profiles.value.push(profile);
  selectProfile(profile.id);
}

// ---------------------------------------------------------------------------
// 037 提醒入口回退：角标单源 = 服务端 /api/job-reminders/count 的 total；
// 列表/total 同源（002 合同），数字与抽屉天然一致；不再合成胶囊派生数。
// ---------------------------------------------------------------------------

const reminderDrawerOpen = ref(false);
const reminderBadge = useReminderBadge(currentProfileId);

// 037：投递提醒 0→N 推一条打断进 carousel（只转一次展示，转完沉入 panel 未读）。
// 仅在从 0 跳到 N 时推——避免每次 +1 都打扰；N→M（都 >0）不推。
// target:"reminders"——沉入 panel 的行点击直达提醒抽屉（复审 P2-8）；
// history 浏览期间经 pushInterruptOrDefer 顺延（复审 P2-5）。
watch(
  () => reminderBadge.reminderTotal.value,
  (next, prev) => {
    if (prev === 0 && next > 0) {
      pushInterruptOrDefer({
        content: { title: "投递提醒", detail: `${next}条逾期`, tone: "warning", target: "reminders" },
        duration: 2200,
      });
    }
  },
);

function toggleReminderDrawer() {
  if (reminderDrawerOpen.value) {
    reminderDrawerOpen.value = false;
    return;
  }
  // profile 为空时不请求也不打开抽屉。
  if (!currentProfileId.value) return;
  collapseIsland();
  reminderDrawerOpen.value = true;
  // 两个抽屉互斥：打开提醒时收起收藏。
  favoritesOpen.value = false;
  closeHistoryDrawer();
}

// 抽屉触发按钮：鼠标左键“按下”即展开/收起，不等松开；
// 键盘 Enter/Space 触发的是 detail===0 的 click，照常响应（可访问性）；
// 鼠标产生的 click（detail>=1）跳过，避免与 mousedown 双重触发。
function handleDrawerTrigger(trigger: () => void, event: MouseEvent) {
  if (event.type === "mousedown") {
    if (event.button === 0) trigger();
    return;
  }
  if (event.detail === 0) trigger();
}

// 037 灵动岛收起（dismiss）→ 只把"关闭瞬间已在面板里的通知"标已读；
// 关闭窗口期（leaving 220ms）新到达的通知不在快照内，保持未读（复审二 N2）。
function handleIslandDismiss(ids: string[]) {
  islandNotices.markReadBatch(ids);
}

// profile 初始化/切换：收起岛面板、关提醒抽屉、清空岛通知池 + carousel 打断
// 队列 + history 期间积压的未消费打断（037）；badge 由 composable 内部 watch
// 自刷新（seq 自守卫），App 不再重复调用，避免双请求。
watch(currentProfileId, (profileId) => {
  collapseIsland();
  reminderDrawerOpen.value = false;
  islandNotices.reset();
  islandCarousel.reset();
  historyPendingInterrupts.length = 0;
});

// 详情动作（DiscoveryView）或抽屉快捷动作（ReminderDrawer）成功后刷新 count。
// 抽屉列表由 ReminderDrawer 自身按服务端刷新结果更新。
function handleJobFeedbackChanged(payload?: { profileId?: string }) {
  // profile 已切换时丢弃旧 action 触发的刷新，不代旧 profile 发请求。
  if (payload?.profileId && payload.profileId !== currentProfileId.value) return;
  void reminderBadge.refreshReminderCount();
  // 收藏/取消收藏属于 interest 变更，同步刷新收藏数量。
  void loadFavorites();
}

// 037 灵动岛展开面板 → 关闭全部顶栏浮层（三抽屉 + 设置菜单 + 主题选择框）。
// 已读不在打开时标记：面板行以未读态渲染（修"未读高亮死代码"），收起
// （dismiss，带 id 快照）时由 handleIslandDismiss 标记快照内通知为已读。
function handleIslandExpand() {
  settingsMenuOpen.value = false;
  themePickerOpen.value = false;
  favoritesOpen.value = false;
  reminderDrawerOpen.value = false;
  closeHistoryDrawer();
}
</script>

<template>
  <div class="app-shell">
    <!-- 万花筒彩蛋主题：整站光场衬底（032，仅该主题挂载） -->
    <KaleidoField v-if="mode === 'kaleido'" />
    <!-- 036 自绘标题栏：仅桌面 EXE 渲染（浏览器模式不显示），页面最顶部 -->
    <WindowTitleBar />
    <header class="app-header">
      <a
        class="brand"
        href="/"
        aria-label="Career Scout 工作台首页"
        @click.prevent="updateInfo ? (updateDialogOpen = true) : undefined"
      >
        <svg class="brand-mark" width="30" height="30" viewBox="0 0 32 32" fill="none" aria-hidden="true">
          <!-- 航海双色玫瑰（定稿）：四芒等长劈半、明暗相间，北芒左半=品牌色 -->
          <path d="M16 3.5 L14 14 L16 16 Z" fill="var(--logo-north)" />
          <path d="M16 3.5 L16 16 L18 14 Z" fill="var(--logo-a)" />
          <path d="M28.5 16 L16 16 L18 18 Z" fill="var(--logo-a)" />
          <path d="M28.5 16 L18 14 L16 16 Z" fill="var(--logo-b)" />
          <path d="M16 28.5 L18 18 L16 16 Z" fill="var(--logo-b)" />
          <path d="M16 28.5 L16 16 L14 18 Z" fill="var(--logo-a)" />
          <path d="M3.5 16 L14 18 L16 16 Z" fill="var(--logo-b)" />
          <path d="M3.5 16 L16 16 L14 14 Z" fill="var(--logo-a)" />
        </svg>
        <span class="brand-name">Career<span class="tick">·</span>Scout</span>
        <span
          v-if="updatesEnabled && updateInfo"
          class="brand-update-dot"
          data-testid="brand-update-dot"
          role="status"
          :aria-label="`发现新版本 v${updateInfo.latest}`"
          title="发现新版本，点击查看更新"
        ></span>
      </a>

      <DynamicIsland
        ref="islandRef"
        :status="roundStatus"
        :notices="islandNotices.notices.value"
        :carousel="islandCarousel"
        @navigate="handleIslandNavigate"
        @expand="handleIslandExpand"
        @dismiss="handleIslandDismiss"
      />

      <div class="header-actions">
        <button
          class="button secondary reminder-trigger"
          type="button"
          data-testid="reminder-trigger"
          :aria-label="reminderBadge.ariaLabel.value"
          :title="reminderBadge.ariaLabel.value"
          :disabled="!currentProfileId"
          @mousedown="handleDrawerTrigger(toggleReminderDrawer, $event)"
          @click="handleDrawerTrigger(toggleReminderDrawer, $event)"
        >
          <Bell :size="18" aria-hidden="true" /><span>提醒</span>
          <em
            v-if="reminderBadge.reminderTotal.value > 0"
            class="fav-badge reminder-badge"
            data-testid="reminder-badge"
            aria-hidden="true"
          >{{ reminderBadge.badgeText.value }}</em>
        </button>
        <button
          ref="favTriggerEl"
          class="button secondary favorites-trigger"
          type="button"
          aria-label="查看收藏"
          title="查看收藏"
          @mousedown="handleDrawerTrigger(toggleFavorites, $event)"
          @click="handleDrawerTrigger(toggleFavorites, $event)"
        >
          <Star :size="18" aria-hidden="true" /><span>收藏</span>
          <em v-if="favorites.length" class="fav-badge">{{ favorites.length }}</em>
        </button>
        <button
          class="button secondary history-trigger"
          type="button"
          data-testid="history-trigger"
          aria-label="历史轮次"
          title="历史轮次"
          @mousedown="handleDrawerTrigger(toggleHistoryDrawer, $event)"
          @click="handleDrawerTrigger(toggleHistoryDrawer, $event)"
        >
          <History :size="18" aria-hidden="true" /><span>历史</span>
        </button>
        <button
          class="icon-button settings-trigger"
          type="button"
          data-testid="settings-trigger"
          aria-label="设置"
          title="设置"
          @click="toggleSettingsMenu"
        >
          <Settings :size="18" aria-hidden="true" />
          <em
            v-if="updatesEnabled && updateInfo"
            class="settings-update-badge"
            data-testid="settings-update-badge"
            aria-hidden="true"
          ></em>
        </button>
        <button
          ref="themeToggleEl"
          class="icon-button theme-toggle"
          type="button"
          data-testid="theme-toggle"
          :aria-label="themeToggleLabel"
          :title="themeToggleLabel"
          :class="{ charging: themeCharging }"
          @click="handleThemeToggle"
          @pointerdown="startThemeCharge"
          @pointerup="cancelThemeCharge"
          @pointerleave="cancelThemeCharge"
          @pointercancel="cancelThemeCharge"
          @contextmenu.prevent
        >
          <span class="theme-icon" aria-hidden="true">
            <Transition name="theme-icon" mode="out-in">
              <Sun v-if="mode === 'dark'" key="sun" :size="18" />
              <Moon v-else key="moon" :size="18" />
            </Transition>
          </span>
        </button>
        <Transition name="theme-picker">
          <div
            v-if="themePickerOpen"
            ref="themePickerEl"
            class="theme-picker"
            data-testid="theme-picker"
          >
            <ThemePickerOptions :current="mode" @select="handleThemePick" />
          </div>
        </Transition>
      </div>
    </header>

    <Transition name="drawer">
      <div
        v-if="favoritesOpen"
        class="fav-drawer-backdrop"
        @mousedown.self="favoritesOpen = false"
      >
        <aside
          ref="favPanelEl"
          class="fav-drawer"
          role="dialog"
          aria-modal="true"
          aria-label="我的收藏"
          tabindex="-1"
          @keydown="handleFavoritesKeydown"
        >
      <header class="fav-drawer-head">
        <div class="fav-drawer-heading">
          <h2>我的收藏</h2>
          <p v-if="favorites.length" class="fav-total">共 {{ favorites.length }} 个岗位</p>
        </div>
        <button type="button" class="icon-button" aria-label="关闭收藏面板" @click="favoritesOpen = false">
          <X :size="20" aria-hidden="true" />
        </button>
      </header>
      <div class="fav-drawer-body">
        <p v-if="!favorites.length" class="fav-drawer-empty">暂无收藏，在结果页点「收藏」即可添加。</p>
        <div v-else class="fav-drawer-list">
          <div
            v-for="job in favorites"
            :key="String(job.job_id || job.id)"
            class="fav-card"
          >
            <a
              class="fav-card-main"
              :href="favoriteJobUrl(job)"
              target="_blank"
              rel="noopener"
            >
              <strong class="fav-card-title">{{ job.title || "未知岗位" }}</strong>
              <span class="fav-card-meta">{{ favoriteMeta(job) }}</span>
              <span class="fav-card-company">{{ job.company || "" }}</span>
            </a>
            <button
              type="button"
              class="fav-card-remove"
              aria-label="取消收藏"
              :disabled="favoriteRemovingIds.has(`${job.profile_id || ''}:${job.job_id || ''}`)"
              @click.stop.prevent="removeFavorite(job)"
            >
              <LoaderCircle v-if="favoriteRemovingIds.has(`${job.profile_id || ''}:${job.job_id || ''}`)" class="spin" :size="16" aria-hidden="true" />
              <X v-else :size="16" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
        </aside>
      </div>
    </Transition>

    <!-- 037 复审：所有信息提示（含 info/success）都融入灵动岛，不再有独立
         notice toast 浮窗（showNotice 统一走 pushInterruptOrDefer）。 -->

    <ReminderDrawer
      :open="reminderDrawerOpen"
      :profile-id="currentProfileId || null"
      @close="reminderDrawerOpen = false"
      @job-feedback-changed="handleJobFeedbackChanged"
    />

    <div class="app-content">
      <!-- 026 B078：profileId 确定前不挂载 DiscoveryView——否则首次挂载时
           profileId 为空，restoreWorkflowState 用空 key 读不到「已结束」标记，
           会把已结束流程误恢复成 02/03 页（刷新闪现）。等 currentProfileId
           从 /api/profiles 就绪后再挂载，恢复判定才拿到正确的 key。 -->
      <div v-if="!currentProfileId" class="app-content-placeholder">正在加载工作台…</div>
      <DiscoveryView
        v-else
        :profile-id="currentProfileId"
        ref="discoveryRef"
        @notify="showNotice"
        @profile-created="acceptCreatedProfile"
        @job-feedback-changed="handleJobFeedbackChanged"
        @round-status="handleRoundStatus"
        @open-browser-accounts="browserAccountsOpen = true"
      />
    </div>

    <AiSettingsDialog
      :open="aiSettingsOpen"
      @close="aiSettingsOpen = false"
    />
    <BrowserAccountsDialog
      :open="browserAccountsOpen"
      @close="browserAccountsOpen = false"
    />
    <EnvCheckDialog
      :open="envCheckOpen"
      @close="envCheckOpen = false"
      @open-browser-accounts="browserAccountsOpen = true; envCheckOpen = false"
      @open-ai-settings="aiSettingsOpen = true; envCheckOpen = false"
    />
    <UpdateDialog
      :open="updateDialogOpen"
      :info="updateInfo"
      @close="updateDialogOpen = false"
      @ignore="ignoreThisVersion"
    />
    <AppSettingsMenu
      :open="settingsMenuOpen"
      :has-update="Boolean(updatesEnabled && updateInfo)"
      :update-version="updateInfo?.latest || ''"
      :checking="updateChecking"
      @close="settingsMenuOpen = false"
      @open-ai-settings="openAiSettingsFromMenu"
      @open-browser-accounts="openBrowserAccountsFromMenu"
      @open-env-check="openEnvCheckFromMenu"
      @manual-update-check="manualUpdateFromMenu"
      @open-github="openGitHub"
    />
  </div>
</template>

<style scoped>
.settings-trigger {
  position: relative;
}

.settings-update-badge {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--danger);
}

.app-content-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  color: var(--text-muted, #888);
  font-size: 14px;
}
</style>

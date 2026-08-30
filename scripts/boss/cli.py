# -*- coding: utf-8 -*-

"""CLI 入口 configure_stdio/stdout/main（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import argparse
from datetime import datetime
import json
import os
import sys
import threading
from scripts.boss.city_map import list_cities
from scripts.boss.constants import DEFAULT_CDP_PORT, DEFAULT_CITY_INPUT, DEFAULT_LOGIN_TIMEOUT, MAX_PAGES
from scripts.boss.detail_parse import load_existing_details
from scripts.boss.output import flush_jobs, merge_details, merge_details_from_lists, merge_jobs, write_csv, write_detail_csv, write_json_atomic
from scripts.boss_cdp_signals import emit_failure_line
import sys as _sys

from webui.logging_setup import get_logger

_logger = get_logger(__name__)

from scripts.boss import runtime
from scripts.boss import browser as _run_browser
from scripts.boss import detail_analyze as _run_analyze_mod
from scripts.boss import detail_scrape as _run_details_mod
from scripts.boss import search as _run_search_mod
from scripts.boss import session_import as _run_session_mod
from scripts.boss import smoke as _run_smoke_mod
from scripts.boss import login

def configure_stdio():
    """Keep console output usable when the active code page cannot encode emoji."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")


class _ThreadAwareStdout:
    """stdout 替身：只捕获创建线程的输出，其他线程转发到 fallback。

    in-process 模式下多个任务与 Flask 请求线程共存于同一进程，
    ``contextlib.redirect_stdout`` 是进程级赋值，直接使用会把其他线程的
    print 也劫持进任务日志。本类按线程分派：任务线程的输出进本 buffer，
    其他线程原样转发到创建时的 ``sys.stdout``（链式收敛回真 stdout）。
    恢复时仅在 ``sys.stdout`` 仍指向自己时还原，避免并发任务的 redirect
    互相覆盖（后退出者恢复成先退出者留下的 buffer）。

    可直接作上下文管理器使用（``with buffer:``），等价于带守卫的
    ``contextlib.redirect_stdout(buffer)``。
    """

    def __init__(self):
        self._tid = threading.get_ident()
        self._fallback = sys.stdout
        self._previous = None

    def write(self, text):  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def flush(self):
        if threading.get_ident() != self._tid and self._fallback is not None:
            try:
                self._fallback.flush()
            except Exception:
                _logger.debug("控制台输出通道刷新失败（忽略）", exc_info=True)


    def __enter__(self):
        self._previous = sys.stdout
        sys.stdout = self
        return self

    def __exit__(self, *exc):
        if sys.stdout is self:
            sys.stdout = self._previous
        self.flush()


class _LineLogBuffer(_ThreadAwareStdout):
    """按行转发任务线程 stdout 到 on_log 回调（programmatic 日志契约 §2.2）。

    遇换行触发 on_log(line)，未换行的尾部在 flush 时补发。
    """

    def __init__(self, on_log):
        super().__init__()
        self._on_log = on_log
        self._buf = ""

    def write(self, s):
        if not s:
            return 0
        if threading.get_ident() != self._tid:
            if self._fallback is not None:
                try:
                    self._fallback.write(s)
                except Exception:
                    _logger.debug("控制台输出通道写入失败（忽略）", exc_info=True)

            return len(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._on_log(line)
        return len(s)

    def flush(self):
        if threading.get_ident() != self._tid:
            super().flush()
            return
        if self._buf:
            self._on_log(self._buf)
            self._buf = ""


# ============================================================
# main
# ============================================================
def main():
    facade_version = getattr(
        _sys.modules.get("scripts.boss_cdp_raw"), "__version__", "")
    p = argparse.ArgumentParser(
        description=f"BOSS直聘抓取 + 分析 (CDP Raw) v{{facade_version}}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
筛选参数示例:
  --scale 305          公司规模 (301=0-20人 302=20-99 303=100-499 304=500-999 305=1000-9999 306=10000+)
  --stage 807          融资阶段 (801=未融资 ... 807=已上市 808=不需要融资)
  --salary 406         薪资范围 (402=3K以下 403=3-5K 404=5-10K 405=10-20K 406=20-50K 407=50K+)
  --experience 105     经验要求 (108=在校生 102=应届生 101=经验不限 103=1年以内 104=1-3年 105=3-5年 106=5-10年 107=10年+)
  --degree 203         学历要求 (209=初中及以下 208=中专/中技 206=高中 202=大专 203=本科 204=硕士 205=博士)
  --industry 1001      行业 (1001=互联网 1002=电商 1003=金融 ...)

城市支持中文: --city 上海  或代码: --city 101020100

示例:
  # 基础搜索
  %(prog)s --keyword "Java 风控" --city 上海 --pages 5

  # 筛选大公司 + 高薪
  %(prog)s --keyword "Java 风控" --scale 305 --salary 406

  # 抓列表 + 详情 + 分析报告
  %(prog)s --keyword "Java 风控" --pages 3 --detail --analysis

  # 只分析已有数据
  %(prog)s --input ~/.career-scout/job-result/boss_jobs_20260609_1200.json --analysis --no-detail

  # 导出 CSV
  %(prog)s --keyword "Java 风控" --pages 3 --format csv

  # 合并旧数据
  %(prog)s --keyword "Java 风控" --pages 3 --merge old_data.json

  # 环境检查
  %(prog)s --check

  # 浏览器/API smoke test
  %(prog)s --smoke-test

  # 启动 Chrome CDP
  %(prog)s --setup-chrome
        """)
    p.add_argument("--version", action="version", version=f"%(prog)s {{facade_version}}")
    p.add_argument("--keyword", default="AI Agent", help="搜索关键词")
    p.add_argument("--city", default=DEFAULT_CITY_INPUT, help=f"城市 (中文名或代码，默认 {DEFAULT_CITY_INPUT})")
    p.add_argument("--pages", type=int, default=3, help=f"抓取页数 (最大 {MAX_PAGES})")
    p.add_argument("--start-page", type=int, default=1,
                   help="从指定页继续抓取（与已有 --output 断点配合）")
    p.add_argument("--output", default=None, help="列表数据输出路径")
    p.add_argument("--detail-output", default=None, help="详情数据输出路径")
    p.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT,
                   help=f"CDP 调试端口 (默认 {DEFAULT_CDP_PORT})")
    p.add_argument("--format", default="json", choices=["json", "csv"],
                   help="输出格式 (默认 json)")
    p.add_argument("--merge", default=None,
                   help="合并已有 JSON 文件 (按 job_id 去重)")

    # 筛选参数
    p.add_argument("--scale", default=None, help="公司规模代码")
    p.add_argument("--stage", default=None, help="融资阶段代码")
    p.add_argument("--salary", default=None, help="薪资范围代码")
    p.add_argument("--experience", default=None, help="经验要求代码")
    p.add_argument("--degree", default=None, help="学历要求代码")
    p.add_argument("--industry", default=None, help="行业代码")
    p.add_argument("--multiBusinessDistrict", default=None, help=argparse.SUPPRESS)

    # 功能开关
    p.add_argument("--detail", action="store_true", default=True, help="抓取详情页 JD（默认开启）")
    p.add_argument("--no-detail", dest="detail", action="store_false", help="不抓取详情页")
    p.add_argument("--max-details", type=int, default=None, help="最多抓几个详情")
    p.add_argument("--enable-parallel", action="store_true", default=False,
                   help="详情抓取启用常驻 tab 池并行（spec 007 ⑧；默认串行）")
    p.add_argument("--tab-pool-size", type=int, default=5,
                   help="常驻 tab 数（1-10，默认 5；仅 --enable-parallel 时生效）")
    p.add_argument("--stagger-min", type=float, default=5.0,
                   help="错峰启动最小间隔秒（默认 5；仅 --enable-parallel 时生效）")
    p.add_argument("--stagger-max", type=float, default=10.0,
                   help="错峰启动最大间隔秒（默认 10；仅 --enable-parallel 时生效）")
    p.add_argument("--gap-min", type=float, default=8.0,
                   help="详情间隔最小秒数（默认 8；防 code:37）")
    p.add_argument("--gap-max", type=float, default=15.0,
                   help="详情间隔最大秒数（默认 15；防 code:37）")
    p.add_argument("--reset-every", type=int, default=3,
                   help="每抓 N 个详情重置一次 session（默认 3；防 code:37）")
    p.add_argument("--simulation-mode", default=None,
                   choices=("stable", "balanced", "extreme"),
                   help="024 详情加载后人形模拟档位（随机等待/滚动/鼠标；"
                        "不传则零仿真，与旧行为一致）")
    p.add_argument("--events-output", default=None,
                   help="详情 terminal safe event 输出路径 (JSONL；每行一个事件，"
                        "仅含 kind/status/job_id/duration_ms/safe_code，"
                        "供 source 批量解析；不传则不写事件文件)")
    p.add_argument("--list-events-output", default=None,
                   help="列表页完成事件输出路径 (JSONL；供 WebUI 页级进度/快照使用)")
    p.add_argument("--combo-key", default=None,
                   help="组合键（keyword|city），用于页级事件身份；不传则内部推导")
    p.add_argument("--skip-login-check", action="store_true",
                   help="跳过组合级登录探测（任务编排层已做过 preflight 时使用）")
    p.add_argument("--analysis", action="store_true", help="输出分析报告")
    p.add_argument("--input", default=None, help="从已有 JSON 文件读取（跳过抓取）")
    p.add_argument("--allow-dom-fallback", action="store_true",
                   help="API 无数据时允许降级 DOM 提取（薪资可能受字体反爬影响，默认关闭）")

    # 工具命令
    p.add_argument("--check", action="store_true", help="运行环境诊断检查")
    p.add_argument("--smoke-test", action="store_true",
                   help="用真实 Chrome/CDP 跑一次 BOSS 搜索 API smoke test（不写结果文件）")
    p.add_argument("--list-cities", nargs="?", const="", default=None,
                   metavar="关键词",
                   help="打印支持的城市列表（可选关键词过滤，如 --list-cities 江）；"
                        "支持全国城市，码表见 data/city_codes.json，运行时自动从 BOSS 同步")
    p.add_argument("--setup-chrome", action="store_true",
                   help="自动启动 Chrome CDP 调试模式")
    p.add_argument("--copy-login-state", action="store_true",
                   help="已停用；不会复制 Chrome 数据库，请使用受控会话导入")
    p.add_argument("--import-boss-session", action="store_true",
                   help="从另一个已授权 CDP 浏览器导入仅限 zhipin.com 的会话")
    p.add_argument("--source-cdp-port", type=int,
                   help="会话导入的源 Chrome CDP 端口（必须与目标端口不同）")
    p.add_argument("--confirm-session-import", action="store_true",
                   help="确认本次显式授权读取并导入源浏览器的 BOSS 会话")
    p.add_argument("--reset-chrome-profile", action="store_true",
                   help="重建 BOSS 专用 Chrome profile，会清除此专用浏览器内的登录态")
    p.add_argument("--no-wait-login", action="store_true",
                   help="--setup-chrome 启动后不等待 BOSS 登录完成")
    p.add_argument("--login-timeout", type=int, default=DEFAULT_LOGIN_TIMEOUT,
                   help=f"--setup-chrome 等待登录完成的秒数 (默认 {DEFAULT_LOGIN_TIMEOUT})")
    p.add_argument("--stop-chrome", action="store_true",
                   help="关闭 BOSS 专用 CDP Chrome（按隔离 profile 精准匹配，不影响主 Chrome）")
    p.add_argument("--close-chrome", action="store_true",
                   help="抓取正常结束后自动关闭专用 Chrome（默认不关；异常退出不触发，保留登录态）")

    args = p.parse_args()

    if args.copy_login_state:
        print("❌ --copy-login-state 已停用：不会复制 Chrome 数据库。")
        print("   请改用 --import-boss-session + --confirm-session-import。")
        sys.exit(1)

    # --check 模式
    if args.check:
        sys.exit(_run_smoke_mod.run_check(args.cdp_port))

    if args.smoke_test:
        sys.exit(_run_smoke_mod.run_smoke_test(args.cdp_port))

    # --list-cities 模式（无需 Chrome/网络依赖，本地静态码表兜底）
    if args.list_cities is not None:
        list_cities(keyword=args.list_cities or None)
        sys.exit(0)

    if args.import_boss_session:
        sys.exit(_run_session_mod.run_import_boss_session(
            source_cdp_port=args.source_cdp_port,
            target_cdp_port=args.cdp_port,
            authorized=args.confirm_session_import,
        ))

    # --setup-chrome 模式
    if args.setup_chrome:
        sys.exit(_run_browser.run_setup_chrome(
            args.cdp_port,
            copy_login_state=args.copy_login_state,
            reset_profile=args.reset_chrome_profile,
            wait_login=not args.no_wait_login,
            login_timeout=args.login_timeout,
        ))

    # --stop-chrome 模式（关闭 BOSS 专用 CDP Chrome，独立命令）
    if args.stop_chrome:
        sys.exit(_run_browser.run_stop_chrome())

    if not runtime.require_runtime_dependencies("requests", "websocket"):
        emit_failure_line("source_unreachable", "运行时依赖缺失（requests/websocket）")
        sys.exit(1)

    # 页数限制
    if args.pages > MAX_PAGES:
        print(f"⚠️ 页数 {args.pages} 超过上限 {MAX_PAGES}，已自动调整为 {MAX_PAGES}")
        args.pages = MAX_PAGES
    if args.start_page < 1 or args.start_page > args.pages:
        print(f"❌ start-page 必须在 1 到 {args.pages} 之间")
        # 参数错误用退出码 3，与 CDP 失联(2)区分，避免 WebUI 误报成浏览器问题。
        emit_failure_line("source_invalid_output", "start-page 参数超出范围")
        sys.exit(3)

    # 收集筛选条件
    filters = {}
    for key in ["scale", "stage", "salary", "experience", "degree", "industry", "multiBusinessDistrict"]:
        val = getattr(args, key)
        if val:
            filters[key] = val

    # 加载或抓取列表
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            list_data = json.load(f)
        print(f"从文件加载 {len(list_data.get('jobs',[]))} 条: {args.input}")
    else:
        # 登录状态检测
        # 登录状态检测：--skip-login-check 仅用于任务编排层已做 preflight 的调用
        if not args.skip_login_check:
            print("检测登录状态...")
            if not login.check_login_state(args.cdp_port):
                print("❌ 未检测到 BOSS直聘登录状态。请先在 Chrome 中登录 zhipin.com。")
                print("   可运行 --check 检查环境，或 --setup-chrome 启动 Chrome。")
                emit_failure_line("source_login_required", "未检测到 BOSS直聘登录状态")
                sys.exit(1)
            print("✅ 已登录\n")
        else:
            print("跳过登录状态检测（任务预检已处理）")

        list_data = _run_search_mod.scrape_list(
            args.keyword, args.city, args.pages, filters, args.output,
            cdp_port=args.cdp_port, fmt=args.format,
            allow_dom_fallback=args.allow_dom_fallback,
            start_page=args.start_page,
            combo_key=args.combo_key,
            list_events_output=args.list_events_output,
        )

    # 合并外部文件
    merged_details = None
    if args.merge:
        merged_jobs = merge_jobs(args.merge, list_data.get("jobs", []))
        list_data["jobs"] = merged_jobs
        list_data["total"] = len(merged_jobs)
        # 重新保存合并结果
        if args.output:
            flush_jobs(args.output, {
                "keyword": list_data.get("keyword", ""),
                "city": list_data.get("city", ""),
                "filters": list_data.get("filters", {}),
                "filter_desc": list_data.get("filter_desc", []),
                "scraped_at": datetime.now().isoformat(),
                "merged_from": args.merge,
            }, merged_jobs)
            print(f"合并结果已保存: {args.output}")
            if args.format == "csv":
                csv_path = args.output.rsplit(".", 1)[0] + ".csv"
                write_csv(csv_path, merged_jobs)
        # 同时加载旧详情，供后续详情抓取/分析合并（按 job_id 去重）
        merged_details = merge_details(args.merge, [])

    # 抓详情
    details = None
    if args.detail and list_data.get("jobs"):
        # 005 US4: 当 --events-output 提供时，把每个岗位的 terminal safe
        # event 写成 JSONL（每行一个事件），供 BossCdpSource.fetch_details_batch
        # 解析/校验。事件只含 kind/status/job_id/duration_ms/safe_code，
        # 不含 JD/凭据/PII（见 _emit_detail_safe_event）。
        events_callback = None
        events_file_handle = None
        if args.events_output:
            try:
                os.makedirs(os.path.dirname(args.events_output) or ".", exist_ok=True)
                events_file_handle = open(args.events_output, "w", encoding="utf-8")
                def events_callback(event, _f=events_file_handle):
                    _f.write(json.dumps(event, ensure_ascii=False) + "\n")
                    _f.flush()
            except OSError as exc:
                print(f"⚠️ 无法写入事件文件 ({args.events_output}): {exc}")
                events_callback = None
                if events_file_handle is not None:
                    try:
                        events_file_handle.close()
                    except OSError:
                        pass
                    events_file_handle = None
        try:
            details = _run_details_mod.scrape_details(
                list_data, args.max_details, args.detail_output,
                cdp_port=args.cdp_port, fmt=args.format,
                event_callback=events_callback,
                enable_parallel=args.enable_parallel,
                tab_pool_size=args.tab_pool_size,
                stagger_range=(args.stagger_min, args.stagger_max),
                inter_job_gap_range=(args.gap_min, args.gap_max),
                reset_every=args.reset_every,
                simulation_mode=args.simulation_mode,
            )
        finally:
            if events_file_handle is not None:
                try:
                    events_file_handle.close()
                except OSError:
                    pass
        # 若处于合并流程，把旧详情并入本次抓取结果并重新落盘，保证 --merge 后详情不丢失
        if merged_details and args.detail_output:
            details = merge_details_from_lists(merged_details, details)
            os.makedirs(os.path.dirname(args.detail_output) or ".", exist_ok=True)
            write_json_atomic(args.detail_output, details)
            print(f"合并详情已保存: {args.detail_output}")
            if args.format == "csv":
                detail_csv = args.detail_output.rsplit(".", 1)[0] + ".csv"
                write_detail_csv(detail_csv, details)

    # 分析
    if args.analysis:
        # 如果有详情文件也加载
        if not details:
            details = load_existing_details(args.input, args.detail_output)
        _run_analyze_mod.analyze(list_data, details, search_keyword=args.keyword)

    # 抓取正常结束后按需收尾（仅成功路径；异常/登录失败走 sys.exit，不会触发，保留登录态）
    if args.close_chrome:
        profile = _run_browser.prepare_cdp_profile(copy_login_state=False, reset=False)
        stopped = _run_browser.stop_cdp_chrome(profile["path"])
        if stopped:
            print(f"\n🧹 已按 --close-chrome 关闭 BOSS 专用 Chrome 进程：{stopped} 个")
        else:
            print("\nℹ️  --close-chrome 未发现运行中的 BOSS 专用 Chrome 进程")


def print_risk_control_report(err):
    """风控停止时的终端醒目报错：第几页挂的、为什么、已抓多少条、建议干啥。"""
    print()
    print("!" * 64)
    print("  抓取已被风控拦截，提前停止（已抓数据没有丢）")
    print("!" * 64)
    print(f"  原因: {err.reason}")
    if err.page is not None:
        print(f"  停在: 第 {err.page} 页")
    print(f"  已抓: {err.scraped_count} 条" +
          (f"，已保存到 {err.output_path}" if err.output_path else ""))
    print()
    print("  建议（按顺序试）:")
    print("    1. 打开 Chrome 里的 BOSS直聘，手动过一次验证码/安全校验")
    print("    2. 歇 30 分钟以上再抓（频繁抓取容易再被拦）")
    print("    3. 仍不行就退出登录后重新扫码登录")
    if err.resume_page is not None:
        print(f"  恢复后可用 --start-page {err.resume_page} 从断点续抓，已抓的不会重抓")
    print("!" * 64)

# 021 B8 T026：保真基线模块级副作用（import 时配置编码容错）
configure_stdio()

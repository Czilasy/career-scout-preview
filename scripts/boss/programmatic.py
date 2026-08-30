# -*- coding: utf-8 -*-

"""run_search_programmatic 组合运行（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

from datetime import datetime
import json
import os
from scripts.boss.cli import _LineLogBuffer, print_risk_control_report
from scripts.boss.constants import DEFAULT_CDP_PORT, MAX_PAGES
from scripts.boss.detail_parse import load_existing_details
from scripts.boss.exceptions import LoginRequiredError, RiskControlError
from scripts.boss.output import flush_jobs, merge_details, merge_details_from_lists, merge_jobs, write_csv, write_detail_csv, write_json_atomic
from scripts.boss.rate_limit import begin_request_run
import sys as _sys
from scripts.boss import browser
from scripts.boss import detail_scrape
from scripts.boss import login
from scripts.boss import runtime
from scripts.boss import detail_analyze
from scripts.boss import search

# ============================================================
# programmatic 入口（EXE 模式 in-process 执行）
# ============================================================
def run_search_programmatic(
    *,
    keyword: str,
    city: str,
    pages: int,
    cdp_port: int = DEFAULT_CDP_PORT,
    filters: dict | None = None,
    output_path: str | None = None,
    detail_output_path: str | None = None,
    detail: bool = True,
    max_details: int | None = None,
    fmt: str = "json",
    start_page: int = 1,
    allow_dom_fallback: bool = False,
    merge: str | None = None,
    analysis: bool = False,
    events_output: str | None = None,
    enable_parallel: bool = False,
    tab_pool_size: int = 5,
    stagger_range: tuple[float, float] = (5.0, 10.0),
    inter_job_gap_range: tuple[float, float] = (8.0, 15.0),
    reset_every: int = 3,
    close_chrome: bool = False,
    on_log=None,
    on_poll=None,
    cancel_event=None,
    combo_key=None,
    on_page_completed=None,
    list_events_output=None,
    skip_login_check: bool = False,
) -> dict:
    """EXE 模式搜索全流程（列表 + 可选详情 + 可选分析/合并）。

    与 CLI main() 的搜索路径语义等价；返回 {"list_data", "details"}；
    失败通过异常表达（见 contract inprocess-runner.md §3）。
    main() 零改动；CLI 路径不受影响。

    - 参数 dict 直传，不经过 argv 文本往返。
    - on_log 非 None 时，contextlib.redirect_stdout 把既有 print 按行转发；
      on_log=None 时输出走原 stdout（等价 CLI）。
    - cancel_event 置位时 search.scrape_list/detail_scrape.scrape_details 抛 SearchCancelled，
      已写产物保留；None 时行为与现状完全一致。
    - skip_login_check=True 时跳过组合级登录探测；任务编排层应在任务开始时
      已执行过 preflight（真实 401/登录墙仍由列表接口失败映射）。
    """
    begin_request_run()
    import contextlib

    filters = dict(filters or {})

    # 页数限制（与 main 一致）
    if pages > MAX_PAGES:
        print(f"⚠️ 页数 {pages} 超过上限 {MAX_PAGES}，已自动调整为 {MAX_PAGES}")
        pages = MAX_PAGES
    if start_page < 1 or start_page > pages:
        raise ValueError(f"start_page 必须在 1 到 {pages} 之间")

    # 运行时依赖（与 main 一致；EXE 模式下依赖已内置）
    if not runtime.require_runtime_dependencies("requests", "websocket"):
        raise RuntimeError("缺少 CDP 运行依赖")

    # 日志转发：on_log 非 None 时用线程感知的行缓冲（buffer 自带
    # 上下文管理器，恢复时带守卫，避免并发任务的 redirect 互相覆盖）
    buffer = _LineLogBuffer(on_log) if on_log is not None else None
    redirect = buffer if buffer is not None else contextlib.nullcontext()
    # 只在真正进入执行上下文后置位，避免早期校验失败把 runtime._run_active 永久留 True。
    runtime.set_run_active(True)
    with redirect:
        try:
            # 登录状态检测
            # 登录状态检测：任务编排层预检通过后可用 --skip-login-check 跳过，
            # 避免每个搜索组合重复探测导致空响应/解析抖动被误判成登录失效。
            if not skip_login_check:
                print("检测登录状态...")
                if not login.check_login_state(cdp_port):
                    print("❌ 未检测到 BOSS直聘登录状态。请先在 Chrome 中登录 zhipin.com。")
                    print("   可运行 --check 检查环境，或 --setup-chrome 启动 Chrome。")
                    raise LoginRequiredError("未检测到 BOSS直聘登录状态")
                print("✅ 已登录\n")
            else:
                print("跳过登录状态检测（任务预检已处理）")

            list_data = search.scrape_list(
                keyword, city, pages, filters, output_path,
                cdp_port=cdp_port, fmt=fmt,
                allow_dom_fallback=allow_dom_fallback,
                start_page=start_page,
                cancel_event=cancel_event, on_poll=on_poll,
                combo_key=combo_key,
                on_page_completed=on_page_completed,
                list_events_output=list_events_output,
            )

            # 合并外部文件
            merged_details = None
            if merge:
                merged_jobs = merge_jobs(merge, list_data.get("jobs", []))
                list_data["jobs"] = merged_jobs
                list_data["total"] = len(merged_jobs)
                if output_path:
                    flush_jobs(output_path, {
                        "keyword": list_data.get("keyword", ""),
                        "city": list_data.get("city", ""),
                        "filters": list_data.get("filters", {}),
                        "filter_desc": list_data.get("filter_desc", []),
                        "scraped_at": datetime.now().isoformat(),
                        "merged_from": merge,
                    }, merged_jobs)
                    print(f"合并结果已保存: {output_path}")
                    if fmt == "csv":
                        csv_path = output_path.rsplit(".", 1)[0] + ".csv"
                        write_csv(csv_path, merged_jobs)
                merged_details = merge_details(merge, [])

            # 抓详情
            details = None
            if detail and list_data.get("jobs"):
                events_callback = None
                events_file_handle = None
                if events_output:
                    try:
                        os.makedirs(os.path.dirname(events_output) or ".", exist_ok=True)
                        events_file_handle = open(events_output, "w", encoding="utf-8")

                        def events_callback(event, _f=events_file_handle):
                            _f.write(json.dumps(event, ensure_ascii=False) + "\n")
                            _f.flush()
                    except OSError as exc:
                        print(f"⚠️ 无法写入事件文件 ({events_output}): {exc}")
                        events_callback = None
                        if events_file_handle is not None:
                            try:
                                events_file_handle.close()
                            except OSError:
                                pass
                            events_file_handle = None
                try:
                    details = detail_scrape.scrape_details(
                        list_data, max_details, detail_output_path,
                        cdp_port=cdp_port, fmt=fmt,
                        event_callback=events_callback,
                        enable_parallel=enable_parallel,
                        tab_pool_size=tab_pool_size,
                        stagger_range=stagger_range,
                        inter_job_gap_range=inter_job_gap_range,
                        reset_every=reset_every,
                        cancel_event=cancel_event, on_poll=on_poll,
                    )
                finally:
                    if events_file_handle is not None:
                        try:
                            events_file_handle.close()
                        except OSError:
                            pass
                if merged_details and detail_output_path:
                    details = merge_details_from_lists(merged_details, details)
                    os.makedirs(os.path.dirname(detail_output_path) or ".", exist_ok=True)
                    write_json_atomic(detail_output_path, details)
                    print(f"合并详情已保存: {detail_output_path}")
                    if fmt == "csv":
                        detail_csv = detail_output_path.rsplit(".", 1)[0] + ".csv"
                        write_detail_csv(detail_csv, details)

            # 分析
            if analysis:
                if not details:
                    details = load_existing_details(None, detail_output_path)
                detail_analyze.analyze(list_data, details, search_keyword=keyword)

            # 抓取正常结束后按需收尾（仅成功路径；异常不触发，保留登录态）
            if close_chrome:
                profile = browser.prepare_cdp_profile(copy_login_state=False, reset=False)
                stopped = browser.stop_cdp_chrome(profile["path"])
                if stopped:
                    print(f"\n🧹 已按 close_chrome 关闭 BOSS 专用 Chrome 进程：{stopped} 个")
                else:
                    print("\nℹ️  close_chrome 未发现运行中的 BOSS 专用 Chrome 进程")

            return {"list_data": list_data, "details": details}
        except RiskControlError as e:
            # 醒目报告经 redirect_stdout 转发 on_log，再原样抛出（等价 __main__ 块）
            print_risk_control_report(e)
            raise
        finally:
            runtime.set_run_active(False)
            if buffer is not None:
                buffer.flush()

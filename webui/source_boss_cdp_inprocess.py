"""BOSS CDP in-process 执行 mixin（自 source_boss_cdp_detail 拆分，等价搬运）。

原 021 拆分后 source_boss_cdp_detail.py 行数顶到宪法红线，本批把
in-process 执行专用逻辑（stdout 收集、argv 翻译、库式调用执行）整体
迁出为新 mixin ``_BossCdpInProcessMixin``，由 ``_BossCdpDetailMixin``
继承组装，对外接口与行为完全不变。

引用方向：本模块只被 source_boss_cdp_detail 引用；仅依赖既有
helpers / 基础模块，不反向引用 detail。
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from scripts import boss_cdp_raw as boss
from webui.logging_setup import get_logger
from webui.process_executor import run_with_deadline
from webui.source_breaker import PageEventPersistenceError
from webui.source_boss_helpers import (
    SCRAPER_FILTER_FIELDS,
    _format_inprocess_failure,
)
from webui.task_runners import _classify_risk_control_reason

_logger = get_logger(__name__)


class _InProcessCapture(boss._ThreadAwareStdout):
    """in-process 模式 stdout 收集器：任务线程输出进缓冲，其余线程转发。

    与子进程模式的 stdout 捕获等价：只收集任务线程的 print，其他线程
    （Flask 请求等）的输出转发回真 stdout，避免日志串线；``tail()``
    返回截断到 ``max_bytes`` 的尾部文本（对齐 ScraperExecutor 语义）。
    """

    def __init__(self, max_bytes: int = 1_000_000):
        super().__init__()
        self._chunks: list[str] = []
        self._size = 0
        self._max = max(1, int(max_bytes))

    def write(self, text):
        if not text:
            return 0
        if threading.get_ident() != self._tid:
            if self._fallback is not None:
                try:
                    self._fallback.write(text)
                except Exception:
                    _logger.debug("降级输出通道写入失败（忽略）", exc_info=True)

            return len(text)
        if self._size < self._max:
            take = text[: self._max - self._size]
            self._chunks.append(take)
            self._size += len(take)
        return len(text)

    def tail(self, max_chars: int | None = None) -> str:
        limit = self._max if max_chars is None else int(max_chars)
        return "".join(self._chunks)[-limit:]


class _BossCdpInProcessMixin:
    """in-process argv 翻译执行器（合同 inprocess-runner §4.3）。

    本 mixin 只承载 in-process 执行路径：把 ``_build_*_command`` 产出的
    argv 翻译成库式调用参数并直接调用，stdout 收集进线程感知 capture，
    与子进程模式的 stdout 捕获等价。实例状态全部来自主体类。
    """

    #: 无值布尔 flag（出现即为 True）。翻译器只识别本类构建的命令，
    #: 其余命令一律视为不可翻译。
    _IN_PROCESS_BOOL_FLAGS = frozenset({
        "no-detail", "detail", "enable-parallel", "analysis",
        "close-chrome", "setup-chrome", "stop-chrome", "smoke-test",
        "skip-login-check",
    })

    def _run_command(
        self, command: list[str], timeout: int, *, on_poll=None, on_page_completed=None,
    ) -> tuple[int, str]:
        if self.in_process:
            return self._run_in_process(command, timeout, on_page_completed=on_page_completed)
        if self._use_default_runner:
            return self._runner(command, timeout, on_poll=on_poll)
        return self._runner(command, timeout)

    def _run_in_process(
        self, command: list[str], timeout: int, *, on_page_completed=None,
    ) -> tuple[int, str]:
        try:
            parsed = self._translate_argv(command)
        except ValueError as exc:
            # 输入文件读取失败/格式非法：显式失败返回，与子进程模式的
            # open()/json.load() 异常等价（不静默空成功，也不向调用方裸抛）
            return (-1, str(exc))
        if parsed is None:
            return (127, "untranslatable_command")
        if parsed.get("kind") == "list" and on_page_completed is not None:
            parsed["params"]["on_page_completed"] = on_page_completed
        try:
            completed, payload = run_with_deadline(
                lambda: self._run_in_process_impl(parsed),
                timeout_seconds=timeout,
                cancel_event=self.cancel_event,
            )
        except boss.SearchCancelled:
            return (-1, "cancelled")
        except boss.CDPUnavailableError as exc:
            return (2, str(exc))
        except ConnectionError as exc:
            # 026：运行中 WebSocket 断开（CDPSession.send 转内置 ConnectionError）
            # 与连接失败同语义 → 退出码 2 → source_cdp_unavailable →
            # 编排层 is_browser_lost 自动重启/暂停，而非落入通用 Exception 分支
            # 被分类成 source_unknown_error 静默标待确认。
            return (2, str(exc))
        except boss.RequestLimitExceededError as exc:
            return (11, str(exc))
        except boss.LoginRequiredError as exc:
            # 016：登录失效走结构化失败行，不再依赖 captured 关键词
            return (1, _format_inprocess_failure("source_login_required", str(exc)))
        except boss.RiskControlError as exc:
            # 016：优先异常自带 code；缺码时用 reason 兜底分类并把结果写进失败行，
            # 与子进程模式共享同一分类契约
            code = str(getattr(exc, "code", "") or "") or _classify_risk_control_reason(exc.reason)
            return (10, _format_inprocess_failure(code, exc.reason))
        except PageEventPersistenceError:
            raise
        except ValueError as exc:
            return (3, str(exc))
        except Exception as exc:
            _logger.exception("in-process 抓取执行失败 type=%s", type(exc).__name__)
            return (-1, "抓取执行失败")
        if not completed:
            # 与 _default_run 的 TimeoutExpired 语义一致 → source_timeout
            raise subprocess.TimeoutExpired(command, timeout)
        return payload

    def _run_in_process_impl(self, parsed: dict) -> tuple[int, str]:
        """实际库式调用；stdout 收集进线程感知 capture，返回 ``(0, tail)``。

        run_search_programmatic / scrape_details 的内部 print 走 sys.stdout，
        本方法期间被 capture 收集（其他线程的输出转发回真 stdout），
        与子进程模式的 stdout 捕获等价。
        """
        capture = _InProcessCapture(max_bytes=self._executor.max_output_bytes)
        with capture:
            if parsed["kind"] == "list":
                boss.run_search_programmatic(**parsed["params"])
            else:  # detail / detail_batch
                boss.scrape_details(**parsed["params"])
        return (0, capture.tail())

    def _translate_argv(self, command: list[str]) -> dict | None:
        """解析本类 ``_build_*_command`` 产出的 argv，返回 ``{kind, params}``。

        无法翻译的命令返回 ``None``。只识别 list-only / detail-only /
        detail-batch 三类；``--setup-chrome`` 等其他命令一律视为不可翻译。
        """
        flags: dict[str, str | bool] = {}
        # 跳过 python_executable（command[0]）和 scraper_path（command[1]）
        i = 2
        while i < len(command):
            token = command[i]
            if not isinstance(token, str) or not token.startswith("--"):
                i += 1
                continue
            flag = token[2:]
            if flag in self._IN_PROCESS_BOOL_FLAGS or i + 1 >= len(command):
                flags[flag] = True
                i += 1
            else:
                flags[flag] = command[i + 1]
                i += 2

        # setup-chrome / stop-chrome / smoke-test 等不可翻译
        if any(k in flags for k in ("setup-chrome", "stop-chrome", "smoke-test")):
            return None
        if "no-detail" in flags:
            return self._translate_list_argv(flags)
        if "events-output" in flags:
            return self._translate_detail_batch_argv(flags)
        if "detail" in flags and "input" in flags:
            return self._translate_detail_argv(flags)
        return None

    def _translate_list_argv(self, flags: dict) -> dict:
        """list-only（--no-detail）→ run_search_programmatic(detail=False)。"""
        filters = {}
        for name in SCRAPER_FILTER_FIELDS:
            val = flags.get(name)
            if val not in (None, False, ""):
                filters[name] = str(val)
        params = {
            "keyword": str(flags.get("keyword", "")),
            "city": str(flags.get("city", "_")),
            "pages": int(flags.get("pages", "1")),
            "cdp_port": int(flags.get("cdp-port", str(self.cdp_port))),
            "output_path": str(flags.get("output", "")),
            "detail": False,
            "skip_login_check": bool(flags.get("skip-login-check", False)),
            "filters": filters,
            "cancel_event": self.cancel_event,
            "combo_key": str(flags.get("combo-key", "") or "") or None,
            "list_events_output": str(flags.get("list-events-output", "") or "") or None,
            "start_page": max(1, int(flags.get("start-page", "1") or "1")),
        }
        return {"kind": "list", "params": params}

    def _translate_detail_argv(self, flags: dict) -> dict:
        """detail-only（--input + --detail + --max-details 1）→ scrape_details。"""
        input_path = str(flags.get("input", ""))
        output_path = str(flags.get("detail-output", ""))
        list_data = self._read_detail_input(input_path)
        params = {
            "list_data": list_data,
            "max_details": int(flags.get("max-details", "1")),
            "output_path": output_path,
            "cdp_port": int(flags.get("cdp-port", str(self.cdp_port))),
            "cancel_event": self.cancel_event,
        }
        return {"kind": "detail", "params": params}

    def _translate_detail_batch_argv(self, flags: dict) -> dict:
        """detail-batch（--events-output + --enable-parallel）→ scrape_details with events。"""
        input_path = str(flags.get("input", ""))
        output_path = str(flags.get("detail-output", ""))
        events_output_path = str(flags.get("events-output", ""))
        list_data = self._read_detail_input(input_path)

        # 清空 events 文件，event_callback 追加写 JSONL（与子进程产物格式一致）
        try:
            Path(events_output_path).write_text("", encoding="utf-8")
        except OSError:
            pass

        def event_callback(event):
            try:
                with open(events_output_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                pass

        gap_min = float(flags.get("gap-min", "8"))
        gap_max = float(flags.get("gap-max", "15"))
        params = {
            "list_data": list_data,
            "max_details": int(flags.get("max-details", "5")),
            "output_path": output_path,
            "cdp_port": int(flags.get("cdp-port", str(self.cdp_port))),
            "event_callback": event_callback,
            "enable_parallel": True,
            "tab_pool_size": int(flags.get("tab-pool-size", "5")),
            "inter_job_gap_range": (gap_min, gap_max),
            "reset_every": int(flags.get("reset-every", "3")),
            "simulation_mode": flags.get("simulation-mode") or None,
            "cancel_event": self.cancel_event,
        }
        return {"kind": "detail_batch", "params": params}

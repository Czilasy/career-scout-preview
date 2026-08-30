# -*- coding: utf-8 -*-

"""CSV/JSON 输出与合并助手（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import csv
from datetime import datetime
import json
import os
import time
from scripts.boss.constants import CSV_COLUMNS, DEFAULT_RESULT_DIR, DETAIL_CSV_COLUMNS
from scripts.boss.constants import log
import sys as _sys

def default_output_path(kind):
    filename = f"boss_{kind}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    return os.path.join(DEFAULT_RESULT_DIR, filename)


def csv_safe_cell(value):
    """Prefix spreadsheet formula characters to prevent CSV formula injection."""
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def write_csv(csv_path, jobs):
    """将 jobs 列表写入 CSV 文件"""
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for j in jobs:
            # 确保每列都有值
            row = {col: csv_safe_cell(j.get(col, "")) for col in CSV_COLUMNS}
            writer.writerow(row)
    print(f"CSV 已保存: {csv_path}")


def write_detail_csv(csv_path, details):
    """将岗位详情列表写入 CSV 文件"""
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for d in details:
            row = {col: d.get(col, "") for col in DETAIL_CSV_COLUMNS}
            if isinstance(row.get("skill_tags"), list):
                row["skill_tags"] = " | ".join(row["skill_tags"])
            writer.writerow({col: csv_safe_cell(row.get(col, "")) for col in DETAIL_CSV_COLUMNS})
    print(f"详情 CSV 已保存: {csv_path}")


# ============================================================
# 增量写入 JSON
# ============================================================
def _replace_with_retry(temp_path, path, retries=3, delay=0.05):
    """os.replace 短暂重试（Windows 下目标文件被占用偶发 OSError）。

    杀软/索引/OneDrive 瞬时锁定通常一次重试即过；重试耗尽抛专门异常
    ``ResultFileWriteError``，供 CLI 顶层映射为独立退出码，绝不误报登录失效。
    """
    from scripts.boss.exceptions import ResultFileWriteError
    last_error = None
    for attempt in range(retries + 1):
        try:
            os.replace(temp_path, path)
            return
        except OSError as exc:
            last_error = exc
            if attempt >= retries:
                raise ResultFileWriteError(
                    f"结果文件写入失败（{path}）：{exc}"
                ) from exc
            time.sleep(delay * (attempt + 1))
    if last_error is not None:
        raise ResultFileWriteError(
            f"结果文件写入失败（{path}）：{last_error}"
        ) from last_error


def write_json_atomic(path, payload):
    """Write a complete sibling file and atomically replace the destination."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = f"{path}.tmp-{os.getpid()}-{time.time_ns()}"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def flush_jobs(path, meta, jobs):
    """每次有新数据就全量刷写（jobs 去重后），保证异常退出也能保留"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # 合并已有文件
    existing_jobs = []
    seen_ids = set()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
            existing_jobs = old.get("jobs", [])
            seen_ids = {j.get("job_id", "") for j in existing_jobs}
        except (OSError, ValueError):
            pass
    for j in jobs:
        if j.get("job_id") not in seen_ids:
            existing_jobs.append(j)
            seen_ids.add(j.get("job_id", ""))
    meta["total"] = len(existing_jobs)
    meta["jobs"] = existing_jobs
    write_json_atomic(path, meta)


# ============================================================
# 合并外部 JSON 文件
# ============================================================
def merge_jobs(external_path, new_jobs):
    """从外部 JSON 加载 jobs，与 new_jobs 按 job_id 合并去重。

    Args:
        external_path: 已有 JSON 文件路径
        new_jobs: 新抓取的 jobs 列表

    Returns:
        合并后的 jobs 列表
    """
    try:
        with open(external_path, "r", encoding="utf-8") as f:
            old_data = json.load(f)
    except (OSError, ValueError) as e:
        log.warning(f"无法加载合并文件 {external_path}: {e}")
        return new_jobs

    old_jobs = old_data.get("jobs", [])
    merged = list(old_jobs)
    seen_ids = {j.get("job_id", "") for j in merged}

    added = 0
    for j in new_jobs:
        if j.get("job_id") not in seen_ids:
            merged.append(j)
            seen_ids.add(j.get("job_id", ""))
            added += 1

    print(f"合并: 旧文件 {len(old_jobs)} 条 + 新抓取 {len(new_jobs)} 条 = {len(merged)} 条 (新增 {added})")
    return merged


def merge_details(external_path, new_details):
    """从外部 JSON 加载详情，与 new_details 按 job_id 合并去重。

    详情文件本身可能是列表结构（scrape_details 输出）或带 jobs/details 键的字典，
    这里都做兼容。优先保留 new_details 中的同名记录（更新覆盖旧值）。

    Args:
        external_path: 已有详情 JSON 文件路径
        new_details: 新抓取的详情列表（可为空）

    Returns:
        合并后的详情列表
    """
    if not external_path:
        return new_details
    try:
        with open(external_path, "r", encoding="utf-8") as f:
            old_data = json.load(f)
    except (OSError, ValueError) as e:
        log.warning(f"无法加载合并详情文件 {external_path}: {e}")
        return new_details

    if isinstance(old_data, list):
        old_details = old_data
    elif isinstance(old_data, dict):
        old_details = old_data.get("details") or old_data.get("jobs") or []
    else:
        old_details = []

    merged = merge_details_from_lists(old_details, new_details)
    print(f"合并详情: 旧文件 {len(old_details)} 条 + 新抓取 {len(new_details)} 条 = {len(merged)} 条")
    return merged


def merge_details_from_lists(old_details, new_details):
    """把两份详情列表按 job_id 合并去重，new_details 优先（同 id 用新覆盖旧）。"""
    by_id = {}
    for d in old_details:
        jid = d.get("job_id", "") if isinstance(d, dict) else ""
        if jid:
            by_id[jid] = d
    for d in new_details:
        jid = d.get("job_id", "") if isinstance(d, dict) else ""
        if jid:
            by_id[jid] = d
    return list(by_id.values())

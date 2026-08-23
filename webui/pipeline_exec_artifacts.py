"""组合产物检查点与冻结清单（021 B7 自 pipeline_exec.py 搬运）。"""

from __future__ import annotations

import time




def _combo_hash(keyword: str, city: str, pages: int, source_filters: dict | None = None) -> str:
    import hashlib
    import json
    filters = dict(source_filters or {})
    blob = json.dumps({"keyword": keyword, "city": city, "target_pages": pages,
                       "source_filters": filters}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()




def _combo_output_path(artifact_dir, combo_key: str) -> str:
    import os
    import re as _re
    if artifact_dir is None:
        base = os.path.join(os.path.expanduser("~"), ".career-scout", "job-result")
    else:
        base = str(artifact_dir)
    os.makedirs(base, exist_ok=True)
    safe = _re.sub(r"[^\w\u4e00-\u9fff]", "", str(combo_key))[:40] or "combo"
    return os.path.join(base, f"pipeline_{safe}_{time.time_ns()}.json")




def get_frozen_artifact_manifest(
    artifact_manifest: dict | None, stage: str,
) -> dict | None:
    """从工作负载的产物清单中返回指定阶段的冻结产物引用（T026）。

    支持分阶段复用规则（research.md Decision 7）：
    - stage="list" → 返回 list 阶段产物（供 detail/rough 复用）
    - stage="detail" → 返回 detail 阶段产物（供 fine 复用 JD）
    - stage="end_to_end" → 始终返回 None（端到端不复用中间结果）

    artifact_manifest 格式示例::

        {"stages": {"list": {"path": "...", "digest": "..."},
                     "detail": {"path": "...", "digest": "..."}}}

    返回 ``None`` 表示该阶段无可用冻结产物。
    """
    if not artifact_manifest or not isinstance(artifact_manifest, dict):
        return None
    if stage == "end_to_end":
        # FR-025: end_to_end 不复用中间结果
        return None
    stages = artifact_manifest.get("stages", {})
    if not isinstance(stages, dict):
        return None
    return stages.get(stage)

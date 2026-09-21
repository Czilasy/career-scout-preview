"""通用搜索配置包的领域服务（Spec 044 B100）。

一套配置包 = 一次已经形成的通用搜索身份：关键词、城市、画像文本与画像
事实。它不按平台拆分，也不保存第三页筛选条件。本域只调用
webui.store_search_packages 暴露的方法，负责快照归一化、完整性/版本校验、
默认命名与稳定错误码；路由层只做参数解析与错误映射。

错误码：
- ``invalid_package``   请求体的快照结构不合法（400）
- ``invalid_name``      名称不是 1-80 字符的文本（400）
- ``package_not_found`` 目标配置包不存在（404）
- ``package_unusable``  库里的快照损坏、版本不支持或字段不完整（409）
"""

from __future__ import annotations

from webui.profile_facts import derive_profile_facts, validate_profile_facts
from webui.store_search_packages import SearchPackageCorruptError

PAYLOAD_VERSION = 1
MAX_NAME_LENGTH = 80
DEFAULT_PACKAGE_NAME = "常用搜索配置"
# 面向用户的稳定文案：不得包含 SQL、路径、堆栈或原始损坏数据。
UNUSABLE_MESSAGE = "这套配置无法完整读取，请重新保存"
NOT_FOUND_MESSAGE = "这套配置已经不存在"


class SearchPackageError(Exception):
    """配置包领域错误：``code`` 是稳定机器码，``message`` 可直接展示给用户。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def default_package_name(keywords: dict, city: dict) -> str:
    """默认名称：首个关键词 + 城市文本；都为空白时回退为通用名称。"""
    word = ""
    selected = keywords.get("selected") or []
    if selected:
        word = str(selected[0])
    else:
        candidates = keywords.get("candidates") or []
        if candidates:
            word = str(candidates[0].get("word") or "")
    city_text = str(city.get("text") or "").strip()
    if word and city_text:
        return f"{word} · {city_text}"[:MAX_NAME_LENGTH]
    return (word or city_text or DEFAULT_PACKAGE_NAME)[:MAX_NAME_LENGTH]


class SearchPackageService:
    """配置包用例：列表、读取、保存、重命名与删除。"""

    def __init__(self, store):
        self.store = store

    # -- 读 ---------------------------------------------------------------

    def list_packages(self) -> list:
        return [_summary(item) for item in self.store.list_search_packages()]

    def get_package(self, package_id) -> dict:
        """读取并完整校验一套配置包；任一项不成立即整包判为不可用。"""
        return _full(self._load(package_id))

    # -- 写 ---------------------------------------------------------------

    def create_package(self, raw) -> dict:
        """保存当前页面状态：总是创建一套新的并列配置包。"""
        draft = _normalize_payload(raw)
        row = self.store.create_search_package(
            name=draft["name"],
            payload_version=PAYLOAD_VERSION,
            keywords=draft["keywords"],
            city=draft["city"],
            profile_summary=draft["profile"]["summary"],
            profile_facts=draft["profile"]["facts"],
        )
        return _full(row)

    def rename_package(self, package_id, name) -> dict:
        """只改名称，不改关键词、城市或画像内容。"""
        try:
            row = self.store.rename_search_package(
                package_id, _normalize_name(name),
            )
        except KeyError as exc:
            raise _not_found() from exc
        return _full(row)

    def delete_package(self, package_id) -> None:
        try:
            self.store.delete_search_package(package_id)
        except KeyError as exc:
            raise _not_found() from exc

    # -- 内部 -------------------------------------------------------------

    def _load(self, package_id) -> dict:
        try:
            row = self.store.get_search_package(package_id)
        except KeyError as exc:
            raise _not_found() from exc
        except SearchPackageCorruptError as exc:
            raise SearchPackageError("package_unusable", UNUSABLE_MESSAGE) from exc
        _validate_stored(row)
        return row


# ---------------------------------------------------------------------------
# 归一化与校验
# ---------------------------------------------------------------------------


def _invalid(message: str) -> SearchPackageError:
    return SearchPackageError("invalid_package", message)


def _not_found() -> SearchPackageError:
    return SearchPackageError("package_not_found", NOT_FOUND_MESSAGE)


def _unusable(_message: str = UNUSABLE_MESSAGE) -> SearchPackageError:
    return SearchPackageError("package_unusable", UNUSABLE_MESSAGE)


def _normalize_name(value) -> str:
    if not isinstance(value, str):
        raise SearchPackageError("invalid_name", "配置名称必须是文本")
    name = value.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise SearchPackageError(
            "invalid_name", f"配置名称长度必须为 1 至 {MAX_NAME_LENGTH} 个字符",
        )
    return name


def _normalize_keywords(raw) -> dict:
    if not isinstance(raw, dict):
        raise _invalid("配置包缺少关键词内容")
    candidates_raw = raw.get("candidates")
    selected_raw = raw.get("selected")
    if not isinstance(candidates_raw, list) or not isinstance(selected_raw, list):
        raise _invalid("关键词内容格式不正确")
    candidates: list = []
    seen: set = set()
    for item in candidates_raw:
        if isinstance(item, str):
            word, recommended = item, False
        elif isinstance(item, dict):
            word = item.get("word")
            recommended = item.get("recommended")
            if not isinstance(word, str):
                raise _invalid("关键词建议缺少文字")
        else:
            raise _invalid("关键词建议格式不正确")
        word = word.strip()
        if not word or word in seen:
            continue
        seen.add(word)
        candidates.append({"word": word, "recommended": bool(recommended)})
    selected: list = []
    seen_selected: set = set()
    for item in selected_raw:
        if not isinstance(item, str):
            raise _invalid("已选关键词格式不正确")
        word = item.strip()
        if not word or word in seen_selected:
            continue
        seen_selected.add(word)
        selected.append(word)
    custom = raw.get("custom")
    return {
        "candidates": candidates,
        "selected": selected,
        "custom": custom if isinstance(custom, str) else "",
    }


def _normalize_city(raw) -> dict:
    if not isinstance(raw, dict):
        raise _invalid("配置包缺少城市内容")
    text = raw.get("text")
    custom = raw.get("custom")
    if not isinstance(text, str) or not isinstance(custom, str):
        raise _invalid("城市内容格式不正确")
    return {"text": text, "custom": custom}


def _normalize_profile(raw) -> dict:
    if not isinstance(raw, dict):
        raise _invalid("配置包缺少画像内容")
    summary = raw.get("summary")
    facts = raw.get("facts")
    if not isinstance(summary, str) or not isinstance(facts, dict):
        raise _invalid("画像内容格式不正确")
    return {"summary": summary, "facts": _normalize_profile_facts(facts, _invalid)}


def _normalize_profile_facts(raw, error_factory) -> dict:
    """按 profile_facts 契约做深层白名单投影。

    画像事实会进入精筛提示词，只允许简历事实契约中的字段；平台 schema、
    第三页筛选、区县/商圈码和原始分析响应即使藏在嵌套对象里也不能落库。
    """
    if not isinstance(raw, dict):
        raise error_factory("画像事实格式不正确")
    return derive_profile_facts(validate_profile_facts(raw))


def _normalize_payload(raw) -> dict:
    """白名单投影：只读取契约内的字段，平台与第三页筛选字段一律不进入快照。"""
    if not isinstance(raw, dict):
        raise _invalid("配置包内容必须是对象")
    version = raw.get("payloadVersion")
    if isinstance(version, bool) or version != PAYLOAD_VERSION:
        raise _invalid("配置包版本不受支持")
    keywords = _normalize_keywords(raw.get("keywords"))
    city = _normalize_city(raw.get("city"))
    profile = _normalize_profile(raw.get("profile"))
    raw_name = raw.get("name")
    if raw_name is None or (isinstance(raw_name, str) and not raw_name.strip()):
        name = default_package_name(keywords, city)
    else:
        name = _normalize_name(raw_name)
    return {"name": name, "keywords": keywords, "city": city, "profile": profile}


def _validate_stored(row: dict) -> None:
    """库内快照完整性校验：只读检查，不做任何修补。"""
    if row.get("payload_version") != PAYLOAD_VERSION:
        raise _unusable()
    name = row.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_LENGTH:
        raise _unusable()
    if not isinstance(row.get("profile_summary"), str):
        raise _unusable()
    facts = row.get("profile_facts")
    if not isinstance(facts, dict):
        raise _unusable()
    try:
        normalized_facts = _normalize_profile_facts(facts, _unusable)
    except SearchPackageError:
        raise _unusable()
    if normalized_facts != facts:
        raise _unusable()
    keywords = row.get("keywords")
    if not isinstance(keywords, dict):
        raise _unusable()
    candidates = keywords.get("candidates")
    selected = keywords.get("selected")
    if not isinstance(candidates, list) or not isinstance(selected, list):
        raise _unusable()
    if not isinstance(keywords.get("custom"), str):
        raise _unusable()
    for item in candidates:
        if not isinstance(item, dict):
            raise _unusable()
        if not isinstance(item.get("word"), str) or not item["word"].strip():
            raise _unusable()
        if not isinstance(item.get("recommended"), bool):
            raise _unusable()
    for item in selected:
        if not isinstance(item, str) or not item.strip():
            raise _unusable()
    city = row.get("city")
    if not isinstance(city, dict):
        raise _unusable()
    if not isinstance(city.get("text"), str) or not isinstance(city.get("custom"), str):
        raise _unusable()


# ---------------------------------------------------------------------------
# 投影
# ---------------------------------------------------------------------------


def _summary(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _full(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "payloadVersion": row["payload_version"],
        "keywords": row["keywords"],
        "city": row["city"],
        "profile": {
            "summary": row["profile_summary"],
            "facts": row["profile_facts"],
        },
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }

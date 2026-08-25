"""AI 设置 / 高级设置 / 浏览器账号 API 路由（021 B6 T019 外迁自 webui/app.py）。

路由体纯搬运：HTTP 契约零改动。store / 旧版高级设置读写 / 登录缓存失效 /
浏览器锁助手经 ctx 取用；可 patch 符号（ai_service / boss 等）经 ctx 动态
门面保住 patch("webui.app.X") 面。
"""

from __future__ import annotations


from flask import jsonify, request

from webui.app import _MSG_ACCOUNT_NOT_FOUND, _MSG_UNSUPPORTED_PLATFORM
from webui.task_runners import _mask_key

def register_settings_routes(app, ctx):
    def _resolve_credentials_from_request():
        """从请求 body 读取 endpoint_url/api_key/model，缺的用已保存设置兜底。

        供 /api/ai-settings/test 和 /api/ai-settings/models 使用：让用户
        不点"保存设置"也能用当前对话框里填的值直接测试/拉取。返回
        (endpoint_url, api_key, model)；endpoint_url 或 api_key 既不在请求
        里也没有已保存值时抛 ValueError。
        """
        raw = request.get_json(silent=True) or {}
        endpoint_url = str(raw.get("endpoint_url") or "").strip()
        api_key = str(raw.get("api_key") or "").strip()
        model = str(raw.get("model") or "").strip()

        settings = ctx.store.get_ai_settings()
        if not endpoint_url:
            endpoint_url = settings.get("endpoint_url") or ""
        if not api_key:
            cred_ref = ctx.store.get_credential_ref()
            api_key = ctx.ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
        if not model:
            model = settings.get("model", "")

        if not endpoint_url:
            raise ValueError("请先填写 AI 服务 URL")
        if not api_key:
            raise ValueError("API Key 未配置")
        return endpoint_url, api_key, model

    @app.route("/api/ai-settings", methods=["GET", "PUT"])
    def ai_settings():
        if request.method == "GET":
            settings = ctx.store.get_ai_settings()
            # 返回打码后的 key 预览（保留首尾，中间星号），供前端展示
            cred_ref = ctx.store.get_credential_ref() if settings.get("is_configured") else ""
            real_key = ctx.ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
            settings["masked_key"] = _mask_key(real_key) if real_key else ""
            return jsonify(settings)
        raw = request.get_json(silent=True) or {}
        endpoint_url = str(raw.get("endpoint_url") or "").strip()
        api_key = str(raw.get("api_key") or "").strip()
        model = str(raw.get("model") or "").strip()
        if not endpoint_url:
            raise ValueError("endpoint_url 不能为空")
        # key 为空时尝试复用已存的 key（允许只改 model 不重填 key）
        if not api_key:
            existing_ref = ctx.store.get_credential_ref()
            api_key = ctx.ai_service.retrieve_api_key(existing_ref) if existing_ref else ""
            if not api_key:
                raise ValueError("api_key 不能为空（尚未保存过 key）")
        credential_ref = ctx.ai_service.store_api_key(endpoint_url, api_key)
        settings = ctx.store.save_ai_settings(
            endpoint_url, credential_ref, status="unconfigured", model=model,
        )
        return jsonify(settings)

    @app.route("/api/ai-settings/test", methods=["POST"])
    def ai_settings_test():
        # 优先用请求 body 里当前对话框填的值；缺的再用已保存设置兜底
        endpoint_url, api_key, model = _resolve_credentials_from_request()
        capability = ctx.ai_service.test_connection(endpoint_url, api_key, model=model)
        new_status = "ready" if capability["ok"] else "failed"
        error_code = capability["warning_codes"][0] if not capability["ok"] and capability["warning_codes"] else None
        ctx.store.update_ai_status(new_status, last_error_code=error_code)
        payload = dict(capability)
        if not capability.get("ok") and error_code:
            payload["user_message"] = ctx.ai_service.user_facing_error(error_code)
        else:
            payload["user_message"] = ""
        return jsonify(payload)

    @app.route("/api/ai-settings/models", methods=["POST"])
    def ai_settings_models():
        """拉取可用模型列表。前端持 key 不安全，由后端代理 GET /models。

        改成 POST：前端可把当前对话框里填的 endpoint_url/api_key/model
        放进 body，不必先点"保存设置"就能拉取。body 里缺的字段回退到
        已保存设置（与 /test 路由一致）。
        """
        endpoint_url, api_key, _model = _resolve_credentials_from_request()
        try:
            models = ctx.ai_service.list_models(endpoint_url, api_key)
        except ctx.ai_service.AISecurityError as exc:
            # 语义修正：AISecurityError 是失败，不应返回 200。
            # 前端 fetchModels 通过 response.ok 判断，502 不影响行为。
            return jsonify({
                "ok": False,
                "error_code": exc.error_code,
                "user_message": ctx.ai_service.user_facing_error(exc.error_code),
                "models": [],
            }), 502
        return jsonify({"ok": True, "models": models})

    @app.route("/api/advanced-settings", methods=["GET"])
    def get_advanced_settings():
        """SPEC011 T009: 返回版本化配置状态。

        保留 ``settings`` 字段用于迁移兼容，同时返回 selection、last_custom、
        mode_version 等版本化状态。
        """
        from webui.execution_config import CONFIG_SCHEMA_VERSION
        from webui.pipeline_exec import _ADVANCED_DEFAULTS
        state = ctx.store.get_advanced_config_state()
        active_version = None
        previous_version = None
        if state["active_mode_version_id"]:
            try:
                active_version = ctx.store.get_mode_version(
                    state["active_mode_version_id"]
                )
                previous_version = ctx.store.get_previous_mode_version(
                    state["active_mode_version_id"]
                )
            except KeyError:
                active_version = None
                previous_version = None
        # 兼容旧前端：settings 仍返回当前活跃设置
        legacy_settings = ctx.load_legacy_advanced_settings()
        return jsonify({
            "ok": True,
            "selection": state["active_selection"],
            "settings": legacy_settings,
            "defaults": _ADVANCED_DEFAULTS,
            "last_custom": {
                "config_digest": state["last_custom_digest"],
                "settings": state["last_custom_config"] or {},
            } if state["last_custom_config"] else None,
            "mode_version": {
                "id": state["active_mode_version_id"],
                "version_digest": active_version["version_digest"],
                "previous_version_id": (
                    previous_version["id"] if previous_version else None
                ),
                "available_modes": ["stable", "balanced", "extreme"],
            } if active_version else None,
            "manual_ranges": (
                active_version["manual_ranges"] if active_version else {}
            ),
            "config_schema_version": CONFIG_SCHEMA_VERSION,
        })

    @app.route("/api/advanced-settings", methods=["POST"])
    def save_advanced_settings_endpoint():
        """SPEC011 T009: 兼容旧 POST 保存，同时写入 store 的自定义配置。"""
        from webui.execution_config import DEFAULT_DETAIL_TAB_POOL_SIZE, SPEED_FIELDS
        from webui.pipeline_exec import _ADVANCED_DEFAULTS
        body = request.get_json(silent=True) or {}
        settings = body.get("settings")
        if not isinstance(settings, dict):
            return jsonify({"ok": False, "error": "缺少 settings 对象"}), 400
        # 只保留合法 key，类型校验
        clean = {}
        for k, default in _ADVANCED_DEFAULTS.items():
            if k in settings:
                val = settings[k]
                if k == "browser_account":
                    from webui.pipeline_exec import load_browser_accounts
                    accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
                    clean[k] = str(val) if str(val) in accounts else "a"
                    continue
                if isinstance(default, float):
                    val = float(val)
                elif isinstance(default, int):
                    val = int(val)
                clean[k] = val
        ctx.save_legacy_advanced_settings(clean)
        # SPEC011: 如果速度字段都存在，也写入 store 自定义配置
        speed_fields = {k: v for k, v in clean.items() if k in SPEED_FIELDS}
        speed_fields.setdefault("detail_tab_pool_size", DEFAULT_DETAIL_TAB_POOL_SIZE)
        if len(speed_fields) == len(SPEED_FIELDS):
            try:
                ctx.store.save_custom_config(speed_fields)
            except (ValueError, TypeError):
                pass  # store 保存失败不阻塞旧路径
        return jsonify({"ok": True, "settings": ctx.load_legacy_advanced_settings()})

    @app.route("/api/browser-accounts", methods=["GET"])
    def list_browser_accounts():
        from webui.pipeline_exec import load_browser_accounts
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        active = str((ctx.load_legacy_advanced_settings() or {}).get("browser_account") or "a")
        if active not in accounts:
            active = "a"
        lock_kind, locked_account, lock_platform = ctx.browser_lock()
        from scripts.login_state_cache import all_login_states
        return jsonify({
            "ok": True,
            "accounts": ctx.project_browser_accounts(accounts),
            "active_account": active,
            "login_states": all_login_states(),
            "busy": ctx.browser_busy(),
            "busy_kind": lock_kind,
            "locked_account": (
                locked_account if lock_kind == "paused" else None
            ),
            "locked_platform": (
                lock_platform if lock_kind is not None else None
            ),
        })

    @app.route("/api/browser-accounts", methods=["POST"])
    def add_browser_account_endpoint():
        from webui.pipeline_exec import add_browser_account
        body = request.get_json(silent=True) or {}
        name = str(body.get("name") or "").strip()
        profile_dir = str(body.get("profile_dir") or "").strip()
        try:
            account = add_browser_account(
                name, profile_dir=profile_dir,
                path=app.config["BROWSER_ACCOUNTS_PATH"],
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        except (OSError, RuntimeError):
            return jsonify({"ok": False, "error": "账号保存失败，请检查磁盘后重试"}), 503
        return jsonify({"ok": True, "account": account}), 201

    @app.route("/api/browser-accounts/<account_id>/activate", methods=["POST"])
    def activate_browser_account(account_id):
        from webui.pipeline_exec import load_browser_accounts
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        if str(account_id) not in accounts:
            return jsonify({"ok": False, "error": _MSG_ACCOUNT_NOT_FOUND}), 404
        lock_kind, _, _ = ctx.browser_lock()
        if lock_kind == "running":
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "当前有任务运行，浏览器正在被占用；请先等待、取消或结束任务",
            }), 409
        if lock_kind == "paused":
            ctx.close_paused_run_browser()
        settings = ctx.load_legacy_advanced_settings()
        settings["browser_account"] = str(account_id)
        ctx.save_legacy_advanced_settings(settings)
        return jsonify({"ok": True, "active_account": str(account_id)})

    @app.route("/api/browser-accounts/<account_id>/roles", methods=["PUT"])
    def set_browser_account_roles(account_id):
        from webui.pipeline_exec import (
            assign_account_role,
            load_browser_accounts,
            save_browser_accounts,
        )
        body = request.get_json(silent=True) or {}
        roles = body.get("roles")
        if not isinstance(roles, list) or not all(
                r in ("R1", "R2") for r in roles):
            return jsonify({"ok": False, "error": "角色取值必须是 R1/R2"}), 422
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        if str(account_id) not in accounts:
            return jsonify({"ok": False, "error": _MSG_ACCOUNT_NOT_FOUND}), 404
        # 完整替换语义：该账号先退出全部角色，再对 roles 中每个角色互斥打标
        # （清全账号该角色 → 打标到该账号），保证角色→账号一对一；保序去重。
        for aid, item in accounts.items():
            if str(aid) == str(account_id):
                item["roles"] = []
        for role in dict.fromkeys(roles):
            accounts = assign_account_role(accounts, role, str(account_id))
        save_browser_accounts(accounts, app.config["BROWSER_ACCOUNTS_PATH"])
        updated = accounts.get(str(account_id)) or {}
        return jsonify({
            "ok": True,
            "account_id": str(account_id),
            "roles": list(updated.get("roles") or []),
        })

    @app.route("/api/browser-accounts/<account_id>/open", methods=["POST"])
    def open_browser_account(account_id):
        from webui.pipeline_exec import (
            ensure_chrome_ready,
            load_browser_accounts,
            set_active_cdp_data_dir,
        )
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        account = accounts.get(str(account_id))
        if account is None:
            return jsonify({"ok": False, "error": _MSG_ACCOUNT_NOT_FOUND}), 404
        body = request.get_json(silent=True) or {}
        platform = str(body.get("platform") or "boss").strip()
        from webui.platforms import (
            derive_zhilian_profile_dir,
            get_platform_or_none,
            resolve_login_space,
        )
        reg = get_platform_or_none(platform)
        if reg is None:
            return jsonify({
                "ok": False, "error_code": "platform_validation_failed",
                "user_message": _MSG_UNSUPPORTED_PLATFORM, "platform": platform,
            }), 400
        boss_profile_dir = str(account.get("profile_dir") or "")
        if not boss_profile_dir:
            return jsonify({
                "ok": False, "error": "profile_missing",
                "message": "账号未配置浏览器资料目录",
            }), 409
        try:
            login_space = resolve_login_space(
                platform, str(account_id), boss_profile_dir=boss_profile_dir,
            )
        except (ValueError, RuntimeError) as exc:
            return jsonify({
                "ok": False, "error": "login_space_invalid", "message": str(exc),
            }), 409
        profile_dir = (
            boss_profile_dir if platform == "boss"
            else derive_zhilian_profile_dir(boss_profile_dir)
        )
        platform_label = reg.display_name
        lock_kind, _, _ = ctx.browser_lock()
        if lock_kind == "paused":
            ctx.close_paused_run_browser()
            set_active_cdp_data_dir(profile_dir)
            ok, msg = ensure_chrome_ready(login_space.cdp_port)
            if not ok:
                return jsonify({
                    "ok": False, "error": "chrome_not_ready", "message": msg,
                }), 409
            ctx.invalidate_login_cache(str(account_id), platform)
            return jsonify({
                "ok": True,
                "message": (
                    f"已打开「{account['name']}」的{platform_label}自动化浏览器，"
                    "请登录后回到任务页点「继续」"
                ),
            })
        if lock_kind is not None:
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "当前有任务运行，浏览器正在被占用；请先等待、取消或结束任务",
            }), 409
        set_active_cdp_data_dir(profile_dir)
        ok, msg = ensure_chrome_ready(login_space.cdp_port)
        if not ok:
            return jsonify({"ok": False, "error": "chrome_not_ready", "message": msg}), 409
        ctx.invalidate_login_cache(str(account_id), platform)
        return jsonify({
            "ok": True,
            "message": f"已打开「{account['name']}」的{platform_label}自动化浏览器，请登录",
        })

    @app.route("/api/browser-accounts/<account_id>", methods=["DELETE"])
    def delete_browser_account_endpoint(account_id):
        from webui.pipeline_exec import (
            close_debug_chrome,
            delete_browser_account,
            load_browser_accounts,
            set_active_cdp_data_dir,
        )
        lock_kind, _, _ = ctx.browser_lock()
        if lock_kind == "running":
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "任务运行中不能删除账号",
            }), 409
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        account = accounts.get(str(account_id))
        if account is None:
            return jsonify({"ok": False, "error": _MSG_ACCOUNT_NOT_FOUND}), 404
        from webui.platforms import derive_zhilian_profile_dir, get_platform_or_none
        zhilian_reg = get_platform_or_none("zhilian")
        zhilian_port = int(zhilian_reg.default_cdp_port) if zhilian_reg else 9223
        boss_profile_dir = str(account.get("profile_dir") or "")
        zhilian_profile_dir = (
            derive_zhilian_profile_dir(boss_profile_dir) if boss_profile_dir else ""
        )

        def _port_profiles(port: int) -> list[str]:
            if not ctx.boss.is_cdp_ready(port):
                return []
            return [ctx.boss.normalize_profile_path(p) for p in ctx.boss.chrome_user_data_dirs_for_cdp_port(port) if p]

        port_profiles_boss = _port_profiles(ctx.boss.DEFAULT_CDP_PORT)
        port_profiles_zhilian = _port_profiles(zhilian_port)
        known_boss = {
            ctx.boss.normalize_profile_path(str(a.get("profile_dir") or ""))
            for a in accounts.values() if str(a.get("profile_dir") or "").strip()
        }
        known_zhilian = {
            ctx.boss.normalize_profile_path(derive_zhilian_profile_dir(
                str(a.get("profile_dir") or "")))
            for a in accounts.values() if str(a.get("profile_dir") or "").strip()
        }
        if boss_profile_dir and ctx.boss.normalize_profile_path(boss_profile_dir) in port_profiles_boss:
            set_active_cdp_data_dir(boss_profile_dir)
            if not close_debug_chrome(ctx.boss.DEFAULT_CDP_PORT):
                return jsonify({
                    "ok": False, "error": "browser_in_use",
                    "message": "该账号的 BOSS 自动化浏览器正在运行，请先打开其他账号或手动关闭后再删除",
                }), 409
        if zhilian_profile_dir and ctx.boss.normalize_profile_path(zhilian_profile_dir) in port_profiles_zhilian:
            set_active_cdp_data_dir(zhilian_profile_dir)
            if not close_debug_chrome(zhilian_port):
                return jsonify({
                    "ok": False, "error": "browser_in_use",
                    "message": "该账号的智联自动化浏览器正在运行，请先打开其他账号或手动关闭后再删除",
                }), 409
        port_profiles_boss = _port_profiles(ctx.boss.DEFAULT_CDP_PORT)
        port_profiles_zhilian = _port_profiles(zhilian_port)
        if boss_profile_dir and ctx.boss.normalize_profile_path(boss_profile_dir) in port_profiles_boss:
            return jsonify({
                "ok": False, "error": "browser_in_use",
                "message": "该账号的 BOSS 自动化浏览器正在运行，请先打开其他账号或手动关闭后再删除",
            }), 409
        if zhilian_profile_dir and ctx.boss.normalize_profile_path(zhilian_profile_dir) in port_profiles_zhilian:
            return jsonify({
                "ok": False, "error": "browser_in_use",
                "message": "该账号的智联自动化浏览器正在运行，请先打开其他账号或手动关闭后再删除",
            }), 409
        for port, profiles, known, label in (
            (ctx.boss.DEFAULT_CDP_PORT, port_profiles_boss, known_boss, "boss"),
            (zhilian_port, port_profiles_zhilian, known_zhilian, "zhilian"),
        ):
            unknown = [p for p in profiles if p and p not in known]
            if unknown:
                return jsonify({
                    "ok": False, "error": "login_space_conflict",
                    "message": f"端口 {port} 被未知 {label} profile 占用，不能删除账号",
                }), 409
        try:
            delete_browser_account(
                str(account_id), path=app.config["BROWSER_ACCOUNTS_PATH"],
            )
        except KeyError:
            return jsonify({"ok": False, "error": _MSG_ACCOUNT_NOT_FOUND}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except (OSError, RuntimeError):
            return jsonify({"ok": False, "error": "账号删除失败，请检查磁盘后重试"}), 503
        settings = ctx.load_legacy_advanced_settings()
        if str(settings.get("browser_account") or "") == str(account_id):
            settings["browser_account"] = "a"
            ctx.save_legacy_advanced_settings(settings)
        return jsonify({"ok": True})

    @app.route("/api/advanced-settings/custom", methods=["PUT"])
    def save_custom_config():
        """SPEC011 T009: 保存完整自定义配置。

        对应 HTTP API PUT /api/advanced-settings/custom。
        """
        from webui.execution_config import SPEED_FIELDS
        body = request.get_json(silent=True) or {}
        settings = body.get("settings")
        if not isinstance(settings, dict):
            return jsonify({"ok": False, "error": "缺少 settings 对象"}), 400
        # 验证速度字段完整；旧 9 字段请求由 store 补默认 JD Tab 数
        missing = [
            f for f in SPEED_FIELDS
            if f not in settings and f != "detail_tab_pool_size"
        ]
        if missing:
            return jsonify({
                "ok": False,
                "error_code": "invalid_request",
                "error": f"缺少必填字段: {missing}",
            }), 400
        try:
            digest = ctx.store.save_custom_config(settings)
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        # 同时保存到旧 JSON 文件以保持兼容
        ctx.save_legacy_advanced_settings(settings)
        state = ctx.store.get_advanced_config_state()
        return jsonify({
            "ok": True,
            "selection": "custom",
            "config_digest": digest,
            "settings": state["last_custom_config"],
        })

    @app.route("/api/advanced-settings/select-mode", methods=["POST"])
    def select_mode():
        """SPEC011 T009: 选择系统参考模式。

        对应 HTTP API POST /api/advanced-settings/select-mode。
        服务端根据 scope_digest 重新计算任务规模，不信任客户端传入的 size。
        """
        body = request.get_json(silent=True) or {}
        mode = body.get("mode")
        scope_digest = body.get("scope_digest")
        if mode not in ("stable", "balanced", "extreme", "custom"):
            return jsonify({
                "ok": False,
                "error_code": "invalid_request",
                "error": "mode 必须是 stable/balanced/extreme/custom",
            }), 400
        scope = ctx.scope_previews.get(str(scope_digest or ""))
        if scope is None:
            return jsonify({
                "ok": False,
                "error_code": "scope_preview_required",
                "error": "搜索范围摘要未知，请重新校验搜索范围",
            }), 409
        task_size = scope["task_size"]
        try:
            result = ctx.store.select_mode(mode, task_size=task_size)
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        # 同时保存到旧 JSON 文件以保持兼容；切回 custom 也必须恢复
        # 最近自定义值，避免 SQLite 权威状态与旧执行入口分叉。
        from webui.execution_config import SPEED_FIELDS
        from webui.mode_configs import MODE_DEFAULT_PAGES
        settings = {
            field: result["config"][field] for field in SPEED_FIELDS
        }
        # 翻页数跟随档位：预设档取档位默认（稳定 2 / 平衡 5 / 极限 10），
        # custom 档取最近自定义值（save_custom_config 已随 last_custom 持久化）。
        if mode in MODE_DEFAULT_PAGES:
            settings["pages"] = MODE_DEFAULT_PAGES[mode]
        else:
            settings["pages"] = int(
                result["config"].get("pages")
                or (ctx.load_legacy_advanced_settings() or {}).get("pages")
                or 3
            )
        # legacy JSON 一并带 pages，保证刷新后 get /api/advanced-settings 返回档位默认。
        legacy_config = dict(result["config"])
        legacy_config["pages"] = settings["pages"]
        ctx.save_legacy_advanced_settings(legacy_config)
        state = ctx.store.get_advanced_config_state()
        return jsonify({
            "ok": True,
            "selection": result["selection"],
            "settings": settings,
            "task_size": task_size,
            "mode_version_id": state["active_mode_version_id"],
            "config_digest": result["config"].get("config_digest"),
        })

    @app.route("/api/advanced-settings/mode-versions/rollback", methods=["POST"])
    def rollback_mode_version():
        """SPEC011 T009: 回退到指定模式版本。

        对应 HTTP API POST /api/advanced-settings/mode-versions/rollback。
        """
        body = request.get_json(silent=True) or {}
        target_version_id = body.get("target_version_id")
        if not target_version_id:
            return jsonify({
                "ok": False,
                "error_code": "invalid_request",
                "error": "缺少 target_version_id",
            }), 400
        try:
            ctx.store.rollback_mode_version(target_version_id)
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        state = ctx.store.get_advanced_config_state()
        return jsonify({
            "ok": True,
            "active_mode_version_id": state["active_mode_version_id"],
        })

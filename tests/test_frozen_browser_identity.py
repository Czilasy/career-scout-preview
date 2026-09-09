"""Contracts for the shared frozen-browser activation boundary."""

from __future__ import annotations

import ast
import pathlib
import unittest
from unittest import mock

from webui import app_support
from webui.frozen_browser_identity import (
    FrozenBrowserBindingError,
    activate_frozen_browser_run,
)


class FrozenBrowserActivationContractTests(unittest.TestCase):
    def test_app_support_delegates_activation_without_platform_branches(self):
        source = pathlib.Path(app_support.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"_activate_run_browser", "_activate_task_browser"}
        }
        self.assertEqual(
            set(functions), {"_activate_run_browser", "_activate_task_browser"}
        )
        for name, function in functions.items():
            with self.subTest(function=name):
                function_source = ast.get_source_segment(source, function) or ""
                self.assertIn("activate_frozen_browser_run", function_source)
                self.assertNotIn("_account_for_run(", function_source)
                self.assertNotRegex(function_source, r"(?:==|!=)\s*['\"](?:boss|zhilian)['\"]")
                self.assertNotRegex(function_source, r"['\"](?:boss|zhilian)['\"]\s*==")

    def test_legacy_boss_account_and_account_profile_key_use_9222(self):
        activated: list[str] = []
        profile = activate_frozen_browser_run(
            {
                "platform": "boss",
                "execution_params": {
                    "platform": "boss",
                    "browser_account": "a",
                    "profile_key": "a",
                },
            },
            fallback_account=lambda _run: "unexpected-fallback",
            accounts_path="unused",
            resolve_account=lambda account, _path: f"/profiles/{account}",
            activate=activated.append,
        )

        self.assertEqual(profile.browser_account, "a")
        self.assertEqual(profile.profile_key, "boss:a")
        self.assertEqual(profile.cdp_port, 9222)
        self.assertEqual(activated, [profile.profile_dir])

    def test_complete_zhilian_identity_never_uses_boss_fallback(self):
        fallback = mock.Mock(side_effect=AssertionError("must not fallback"))
        activated: list[str] = []

        profile = activate_frozen_browser_run(
            {
                "platform": "zhilian",
                "execution_params": {
                    "platform": "zhilian",
                    "browser_account": "a",
                    "profile_key": "zhilian:a",
                    "cdp_port": 9223,
                    "execution_config": {},
                },
            },
            fallback_account=fallback,
            accounts_path="unused",
            resolve_account=lambda account, _path: f"/profiles/{account}",
            activate=activated.append,
        )

        self.assertEqual(profile.platform, "zhilian")
        self.assertEqual(profile.profile_key, "zhilian:a")
        self.assertEqual(profile.cdp_port, 9223)
        fallback.assert_not_called()
        self.assertEqual(activated, [profile.profile_dir])

    def test_missing_zhilian_identity_is_strict_and_does_not_fallback(self):
        fallback = mock.Mock(return_value="a")

        with self.assertRaises(FrozenBrowserBindingError):
            activate_frozen_browser_run(
                {
                    "platform": "zhilian",
                    "execution_params": {
                        "platform": "zhilian",
                        "execution_config": {},
                    },
                },
                fallback_account=fallback,
                accounts_path="unused",
                resolve_account=lambda account, _path: f"/profiles/{account}",
                activate=lambda _profile: None,
            )

        fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()

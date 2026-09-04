"""V4 账号克隆隔离：BOSS/智联不得共享账号级可变状态。"""

from __future__ import annotations

import unittest
from unittest import mock

from webui.account_round_robin import clone_source
from webui.source import BossCdpSource, ZhilianCdpSource
from webui.source_breaker import SourceCircuitBreaker


class SourceAccountIsolationV4Tests(unittest.TestCase):
    def test_boss_clone_has_independent_breaker_and_executor_probes(self):
        source = BossCdpSource(
            browser_account="a",
            cdp_port=9222,
            breaker=SourceCircuitBreaker(),
            runner=mock.Mock(),
        )
        source._executor.on_spawn = mock.Mock()
        source._executor.on_output_probe = mock.Mock()

        clone = clone_source(source, "b", run_id="run-b")

        self.assertIsNot(clone.breaker, source.breaker)
        self.assertIsNot(clone._executor, source._executor)
        self.assertIsNone(clone._executor.on_spawn)
        self.assertIsNone(clone._executor.on_output_probe)
        source.breaker.record_signal("source_rate_limited")
        source.breaker.record_signal("source_rate_limited")
        self.assertTrue(source.breaker.is_open())
        self.assertFalse(clone.breaker.is_open())

    def test_zhilian_clone_has_independent_breaker(self):
        source = ZhilianCdpSource(
            browser_account="a",
            cdp_port=9223,
            breaker=SourceCircuitBreaker(),
            preflight_runner=mock.Mock(return_value="ok"),
            list_runner=mock.Mock(),
            detail_runner=mock.Mock(),
            batch_detail_runner=mock.Mock(),
        )

        clone = clone_source(source, "b", run_id="run-b")

        self.assertIsNot(clone.breaker, source.breaker)
        source.breaker.record_signal("source_verification_required")
        source.breaker.record_signal("source_verification_required")
        self.assertTrue(source.breaker.is_open())
        self.assertFalse(clone.breaker.is_open())


if __name__ == "__main__":
    unittest.main()

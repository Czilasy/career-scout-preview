"""Direct unit tests for the shared HTTPS URL allowlist helper."""

from __future__ import annotations

import unittest
from urllib.parse import urlparse

from webui.url_safety import (
    clean_https_url,
    is_safe_https_authority,
    parse_https_url,
)


class ParseHttpsUrlTests(unittest.TestCase):
    def test_accepts_https(self):
        parsed = parse_https_url("https://www.zhipin.com/job_detail/abc.html?x=1#y")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.scheme, "https")

    def test_rejects_http_and_malformed(self):
        self.assertIsNone(parse_https_url("http://www.zhipin.com/"))
        self.assertIsNone(parse_https_url("not a url"))
        self.assertIsNone(parse_https_url(""))
        self.assertIsNone(parse_https_url(None))


class IsSafeHttpsAuthorityTests(unittest.TestCase):
    def test_allows_subdomain_with_flag(self):
        parsed = parse_https_url("https://www.zhipin.com/job_detail/abc.html")
        self.assertTrue(is_safe_https_authority(
            parsed, allowed_hosts={"zhipin.com"}, allow_subdomains=True
        ))

    def test_rejects_userinfo(self):
        parsed = parse_https_url("https://user:pass@www.zhipin.com/job_detail/abc.html")
        self.assertFalse(is_safe_https_authority(
            parsed, allowed_hosts={"zhipin.com"}, allow_subdomains=True
        ))

    def test_rejects_non_443_port(self):
        parsed = parse_https_url("https://www.zhipin.com:8443/job_detail/abc.html")
        self.assertFalse(is_safe_https_authority(
            parsed, allowed_hosts={"zhipin.com"}, allow_subdomains=True
        ))

    def test_accepts_explicit_443_port(self):
        parsed = parse_https_url("https://www.zhipin.com:443/job_detail/abc.html")
        self.assertTrue(is_safe_https_authority(
            parsed, allowed_hosts={"zhipin.com"}, allow_subdomains=True
        ))

    def test_rejects_other_host_and_scheme(self):
        http = urlparse("http://www.zhipin.com/")
        self.assertFalse(is_safe_https_authority(
            http, allowed_hosts={"zhipin.com"}, allow_subdomains=True
        ))
        evil = parse_https_url("https://zhipin.evil.com/")
        self.assertFalse(is_safe_https_authority(
            evil, allowed_hosts={"zhipin.com"}, allow_subdomains=True
        ))

    def test_exact_host_when_subdomains_disabled(self):
        exact = parse_https_url("https://zhipin.com/job")
        sub = parse_https_url("https://www.zhipin.com/job")
        self.assertTrue(is_safe_https_authority(
            exact, allowed_hosts={"zhipin.com"}
        ))
        self.assertFalse(is_safe_https_authority(
            sub, allowed_hosts={"zhipin.com"}
        ))


class CleanHttpsUrlTests(unittest.TestCase):
    def test_drops_query_fragment_and_params(self):
        parsed = parse_https_url(
            "https://www.zhipin.com/job_detail/abc.html;param?x=1#y"
        )
        self.assertEqual(
            clean_https_url(parsed, drop_params=True),
            "https://www.zhipin.com/job_detail/abc.html",
        )


if __name__ == "__main__":
    unittest.main()

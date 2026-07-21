"""Tests for webui.resume: format validation, size limit,
delete behaviour and path-traversal hardening (T013)."""

from __future__ import annotations

import hashlib
import pathlib
import unittest
from unittest import mock

from webui.resume import (
    build_safe_path,
    delete_resume,
    save_resume,
    validate_format,
    validate_size,
)
from webui.store import TaskStore
from tests.test_workbench_fixtures import (
    sample_docx_bytes,
    sample_pdf_bytes,
    sample_resume_text,
    temp_state_layout,
)


# ---------------------------------------------------------------------------
# Format validation
# ---------------------------------------------------------------------------

class FormatValidationTests(unittest.TestCase):
    def test_accepts_txt(self):
        self.assertEqual(validate_format("resume.txt"), "txt")

    def test_accepts_pdf(self):
        self.assertEqual(validate_format("resume.pdf"), "pdf")

    def test_accepts_docx(self):
        self.assertEqual(validate_format("resume.docx"), "docx")

    def test_is_case_insensitive(self):
        self.assertEqual(validate_format("Resume.TXT"), "txt")
        self.assertEqual(validate_format("Resume.PDF"), "pdf")
        self.assertEqual(validate_format("Resume.DOCX"), "docx")

    def test_rejects_doc(self):
        with self.assertRaises(ValueError):
            validate_format("resume.doc")

    def test_rejects_no_extension(self):
        with self.assertRaises(ValueError):
            validate_format("resume")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            validate_format("")

    def test_rejects_none(self):
        with self.assertRaises(ValueError):
            validate_format(None)


# ---------------------------------------------------------------------------
# Size validation
# ---------------------------------------------------------------------------

class SizeValidationTests(unittest.TestCase):
    def test_accepts_at_limit(self):
        validate_size(b"x" * 10_000_000)

    def test_rejects_over_limit(self):
        with self.assertRaises(ValueError):
            validate_size(b"x" * 10_000_001)

    def test_rejects_empty_bytes(self):
        with self.assertRaises(ValueError):
            validate_size(b"")

    def test_rejects_none(self):
        with self.assertRaises(ValueError):
            validate_size(None)

    def test_custom_limit(self):
        validate_size(b"x" * 100, max_bytes=100)
        with self.assertRaises(ValueError):
            validate_size(b"x" * 101, max_bytes=100)


# ---------------------------------------------------------------------------
# build_safe_path
# ---------------------------------------------------------------------------

class BuildSafePathTests(unittest.TestCase):
    def test_returns_relative_basename(self):
        with temp_state_layout() as (_root, _state, _result, resume_dir):
            path = build_safe_path(resume_dir, "abc123", "pdf")
            self.assertEqual(path, "abc123.pdf")
            self.assertNotIn("/", path)
            self.assertNotIn("\\", path)
            self.assertNotIn("..", path)

    def test_rejects_dotdot_in_resume_id(self):
        with temp_state_layout() as (_root, _state, _result, resume_dir):
            with self.assertRaises(ValueError):
                build_safe_path(resume_dir, "../../evil", "txt")

    def test_rejects_separator_in_resume_id(self):
        with temp_state_layout() as (_root, _state, _result, resume_dir):
            with self.assertRaises(ValueError):
                build_safe_path(resume_dir, "sub/dir", "txt")

    def test_rejects_empty_resume_id(self):
        with temp_state_layout() as (_root, _state, _result, resume_dir):
            with self.assertRaises(ValueError):
                build_safe_path(resume_dir, "", "txt")

    def test_rejects_bad_format(self):
        with temp_state_layout() as (_root, _state, _result, resume_dir):
            with self.assertRaises(ValueError):
                build_safe_path(resume_dir, "abc123", "rtf")


# ---------------------------------------------------------------------------
# save_resume integration
# ---------------------------------------------------------------------------

class SaveResumeTests(unittest.TestCase):
    def _setup(self, state_dir, resume_dir):
        store = TaskStore(pathlib.Path(state_dir) / "test.db")
        profile = store.create_profile("测试画像")
        return store, profile

    def test_save_txt_resume(self):
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, profile = self._setup(state_dir, resume_dir)
            file_bytes = sample_resume_text().encode("utf-8")
            result = save_resume(
                profile["id"], file_bytes, "resume.txt", "txt", resume_dir, store
            )
            self.assertEqual(result["format"], "txt")
            self.assertEqual(result["extracted_text"], "")
            self.assertEqual(
                result["content_hash"], hashlib.sha256(file_bytes).hexdigest()
            )
            self.assertEqual(result["original_filename"], "resume.txt")
            file_path = pathlib.Path(resume_dir) / result["storage_path"]
            self.assertTrue(file_path.is_file())

    def test_save_pdf_resume(self):
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, profile = self._setup(state_dir, resume_dir)
            file_bytes = sample_pdf_bytes()
            result = save_resume(
                profile["id"], file_bytes, "resume.pdf", "pdf", resume_dir, store
            )
            self.assertEqual(result["format"], "pdf")
            self.assertEqual(result["extracted_text"], "")
            self.assertEqual(
                result["content_hash"], hashlib.sha256(file_bytes).hexdigest()
            )
            file_path = pathlib.Path(resume_dir) / result["storage_path"]
            self.assertTrue(file_path.is_file())

    def test_save_docx_resume(self):
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, profile = self._setup(state_dir, resume_dir)
            file_bytes = sample_docx_bytes()
            result = save_resume(
                profile["id"], file_bytes, "resume.docx", "docx", resume_dir, store
            )
            self.assertEqual(result["format"], "docx")
            self.assertEqual(result["extracted_text"], "")
            self.assertEqual(
                result["content_hash"], hashlib.sha256(file_bytes).hexdigest()
            )
            file_path = pathlib.Path(resume_dir) / result["storage_path"]
            self.assertTrue(file_path.is_file())

    def test_save_long_file_no_local_extraction(self):
        """Long files are stored as-is; no local text extraction or truncation."""
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, profile = self._setup(state_dir, resume_dir)
            long_text = "B" * 60_000
            file_bytes = long_text.encode("utf-8")
            result = save_resume(
                profile["id"], file_bytes, "long.txt", "txt", resume_dir, store
            )
            self.assertEqual(result["extracted_text"], "")

    def test_save_rejects_oversize_file(self):
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, profile = self._setup(state_dir, resume_dir)
            big_bytes = b"x" * (10_000_001)
            with self.assertRaises(ValueError):
                save_resume(
                    profile["id"], big_bytes, "big.txt", "txt", resume_dir, store
                )

    def test_save_rejects_bad_extension(self):
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, profile = self._setup(state_dir, resume_dir)
            with self.assertRaises(ValueError):
                save_resume(
                    profile["id"], b"data", "resume.rtf", "rtf", resume_dir, store
                )

    def test_storage_path_is_relative(self):
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, profile = self._setup(state_dir, resume_dir)
            file_bytes = sample_resume_text().encode("utf-8")
            result = save_resume(
                profile["id"], file_bytes, "resume.txt", "txt", resume_dir, store
            )
            storage_path = pathlib.Path(result["storage_path"])
            self.assertFalse(storage_path.is_absolute())
            # Anchored inside resume_dir when resolved.
            resolved = (pathlib.Path(resume_dir) / storage_path).resolve()
            self.assertTrue(
                str(resolved).startswith(str(pathlib.Path(resume_dir).resolve()))
            )


# ---------------------------------------------------------------------------
# delete_resume integration
# ---------------------------------------------------------------------------

class DeleteResumeTests(unittest.TestCase):
    def _setup(self, state_dir, resume_dir):
        store = TaskStore(pathlib.Path(state_dir) / "test.db")
        profile = store.create_profile("测试画像")
        file_bytes = sample_resume_text().encode("utf-8")
        resume = save_resume(
            profile["id"], file_bytes, "resume.txt", "txt", resume_dir, store
        )
        return store, resume

    def test_delete_removes_file_and_wipes_fields(self):
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, resume = self._setup(state_dir, resume_dir)
            rid = resume["id"]
            storage_path = resume["storage_path"]
            file_path = pathlib.Path(resume_dir) / storage_path
            self.assertTrue(file_path.is_file())

            delete_resume(rid, store, resume_dir)

            self.assertFalse(file_path.is_file())
            record = store.get_resume(rid)
            self.assertIsNone(record["extracted_text"])
            self.assertIsNone(record["content_hash"])
            self.assertIsNone(record["original_filename"])
            self.assertIsNotNone(record["deleted_at"])

    def test_delete_when_file_already_missing(self):
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, resume = self._setup(state_dir, resume_dir)
            rid = resume["id"]
            file_path = pathlib.Path(resume_dir) / resume["storage_path"]
            # Remove the physical file before delete_resume runs.
            file_path.unlink()
            self.assertFalse(file_path.is_file())

            # Must not raise.
            delete_resume(rid, store, resume_dir)

            record = store.get_resume(rid)
            self.assertIsNone(record["extracted_text"])
            self.assertIsNone(record["content_hash"])
            self.assertIsNotNone(record["deleted_at"])

    def test_delete_when_text_and_hash_already_null(self):
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, resume = self._setup(state_dir, resume_dir)
            rid = resume["id"]
            # Simulate a state where text and hash were already wiped.
            with store._connection() as conn:
                conn.execute(
                    "UPDATE resumes SET extracted_text = NULL, content_hash = NULL "
                    "WHERE id = ?",
                    (rid,),
                )

            delete_resume(rid, store, resume_dir)

            record = store.get_resume(rid)
            self.assertIsNone(record["extracted_text"])
            self.assertIsNone(record["content_hash"])
            self.assertIsNone(record["original_filename"])
            self.assertIsNotNone(record["deleted_at"])

    def test_delete_without_resume_dir_wipes_db_fields(self):
        """When resume_dir is unknown the file may survive, but DB fields are wiped."""
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, resume = self._setup(state_dir, resume_dir)
            rid = resume["id"]

            delete_resume(rid, store)  # no resume_dir

            record = store.get_resume(rid)
            self.assertIsNone(record["extracted_text"])

    def test_file_delete_failure_preserves_resume_record_for_retry(self):
        """A failed unlink must not erase metadata while the original file remains."""
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, resume = self._setup(state_dir, resume_dir)
            with mock.patch("pathlib.Path.unlink", side_effect=OSError("denied")):
                with self.assertRaises(OSError):
                    delete_resume(resume["id"], store, resume_dir)
            record = store.get_resume(resume["id"])
            self.assertEqual(record["storage_path"], resume["storage_path"])
            self.assertIsNotNone(record["extracted_text"])
            self.assertIsNotNone(record["content_hash"])
            self.assertEqual(record["original_filename"], "resume.txt")
            self.assertIsNone(record["deleted_at"])


# ---------------------------------------------------------------------------
# Path traversal hardening
# ---------------------------------------------------------------------------

class PathTraversalTests(unittest.TestCase):
    def _setup(self, state_dir):
        store = TaskStore(pathlib.Path(state_dir) / "test.db")
        profile = store.create_profile("测试画像")
        return store, profile

    def test_filename_with_dotdot_stays_in_resume_dir(self):
        with temp_state_layout() as (root, state_dir, _result, resume_dir):
            store, profile = self._setup(state_dir)
            malicious = "../../evil.txt"
            file_bytes = sample_resume_text().encode("utf-8")
            result = save_resume(
                profile["id"], file_bytes, malicious, "txt", resume_dir, store
            )
            # storage_path must be a safe relative name without traversal.
            self.assertNotIn("..", result["storage_path"])
            # original_filename is the basename only.
            self.assertEqual(result["original_filename"], "evil.txt")
            # File is inside resume_dir, not at root or above.
            file_path = pathlib.Path(resume_dir) / result["storage_path"]
            self.assertTrue(file_path.is_file())
            evil_path = pathlib.Path(resume_dir) / ".." / ".." / "evil.txt"
            self.assertFalse(evil_path.resolve().is_file())
            # Nothing escaped to the project root.
            root_evil = pathlib.Path(root) / "evil.txt"
            self.assertFalse(root_evil.is_file())

    def test_filename_with_windows_traversal_stays_in_resume_dir(self):
        with temp_state_layout() as (_root, state_dir, _result, resume_dir):
            store, profile = self._setup(state_dir)
            malicious = "..\\..\\evil.txt"
            file_bytes = sample_resume_text().encode("utf-8")
            result = save_resume(
                profile["id"], file_bytes, malicious, "txt", resume_dir, store
            )
            self.assertNotIn("..", result["storage_path"])
            file_path = pathlib.Path(resume_dir) / result["storage_path"]
            self.assertTrue(file_path.is_file())

    def test_build_safe_path_rejects_traversal_id(self):
        with temp_state_layout() as (_root, _state, _result, resume_dir):
            with self.assertRaises(ValueError):
                build_safe_path(resume_dir, "../../evil", "txt")
            with self.assertRaises(ValueError):
                build_safe_path(resume_dir, "..", "pdf")
            with self.assertRaises(ValueError):
                build_safe_path(resume_dir, "a/b", "docx")


if __name__ == "__main__":
    unittest.main()

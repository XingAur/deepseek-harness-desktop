from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from app.code_evidence_artifacts import EvidenceArtifactStore


class EvidenceArtifactStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="his_harness_code_evidence_")
        self.root = Path(self.temp_dir.name) / "evidence"
        self.store = EvidenceArtifactStore(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_atomic_persist_reopen_and_seal_use_exact_hash_size_and_private_modes(self) -> None:
        content = b"diff --git a/a.py b/a.py\n"
        record = self.store.persist(1, kind="diff_patch", leaf="full.patch", content=content)

        self.assertEqual(hashlib.sha256(content).hexdigest(), record.sha256)
        self.assertEqual(len(content), record.size_bytes)
        self.assertEqual("bundle_1/full.patch", record.relative_path)
        self.assertEqual(0o600, (self.root / record.relative_path).stat().st_mode & 0o777)
        self.assertEqual(content, self.store.reopen(record))

        seal = self.store.seal(1, artifacts=(record,), repository_snapshot_sha256="a" * 64)
        self.assertEqual("bundle_1/seal.json", seal.relative_path)
        self.assertEqual(0o700, (self.root / "bundle_1").stat().st_mode & 0o777)
        self.assertEqual(self.store.reopen(seal), (self.root / "bundle_1" / "seal.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "code_evidence_bundle_sealed"):
            self.store.persist(1, kind="source", leaf="late.txt", content=b"late")

    def test_reopen_rejects_byte_inode_truncate_extend_symlink_and_hardlink_changes(self) -> None:
        mutations = ("bytes", "inode", "truncate", "extend", "symlink", "hardlink")
        for index, mutation in enumerate(mutations, 1):
            with self.subTest(mutation=mutation):
                record = self.store.persist(index, kind="source", leaf="source.txt", content=b"original")
                path = self.root / record.relative_path
                if mutation == "bytes":
                    path.write_bytes(b"changed!")
                elif mutation == "inode":
                    replacement = path.with_name("replacement")
                    replacement.write_bytes(b"original")
                    os.replace(replacement, path)
                elif mutation == "truncate":
                    path.write_bytes(b"orig")
                elif mutation == "extend":
                    path.write_bytes(b"original-more")
                elif mutation == "symlink":
                    path.unlink()
                    path.symlink_to(self.root / "outside")
                else:
                    outside = self.root / f"outside-{index}"
                    os.link(path, outside)
                with self.assertRaisesRegex(ValueError, "code_evidence_artifact_changed"):
                    self.store.reopen(record)

    def test_root_bundle_and_parent_symlinks_are_rejected_without_external_write(self) -> None:
        outside = Path(self.temp_dir.name) / "outside"
        outside.mkdir()
        linked_root = Path(self.temp_dir.name) / "linked-root"
        linked_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "code_evidence_artifact_root_invalid"):
            EvidenceArtifactStore(linked_root)

        self.store.persist(1, kind="source", leaf="first.txt", content=b"first")
        bundle = self.root / "bundle_2"
        bundle.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "code_evidence_artifact_path_invalid"):
            self.store.persist(2, kind="source", leaf="second.txt", content=b"second")
        self.assertEqual([], list(outside.iterdir()))

    def test_invalid_leaf_kind_special_content_and_budgets_fail_closed(self) -> None:
        for leaf in ("../outside", "a/b", ".git", "secret.pem", "", "a//b"):
            with self.subTest(leaf=leaf), self.assertRaisesRegex(ValueError, "code_evidence_artifact_input_invalid"):
                self.store.persist(1, kind="source", leaf=leaf, content=b"safe")
        with self.assertRaisesRegex(ValueError, "code_evidence_artifact_input_invalid"):
            self.store.persist(1, kind="authorization", leaf="auth.txt", content=b"safe")
        with self.assertRaisesRegex(ValueError, "code_evidence_artifact_limit_exceeded"):
            self.store.persist(1, kind="source", leaf="large.txt", content=b"x" * (8 * 1024 * 1024 + 1))

    def test_bundle_entry_replacement_after_open_is_rejected(self) -> None:
        original_verify = self.store._verify_bundle_entry
        replaced = False

        def replace(bundle_id: int, bundle_fd: int) -> None:
            nonlocal replaced
            if not replaced:
                replaced = True
                bundle = self.root / f"bundle_{bundle_id}"
                detached = self.root / "detached"
                bundle.rename(detached)
                bundle.mkdir(mode=0o700)
            original_verify(bundle_id, bundle_fd)

        self.store._verify_bundle_entry = replace  # type: ignore[method-assign]
        with self.assertRaisesRegex(ValueError, "code_evidence_artifact_path_changed"):
            self.store.persist(1, kind="source", leaf="source.txt", content=b"safe")


if __name__ == "__main__":
    unittest.main()

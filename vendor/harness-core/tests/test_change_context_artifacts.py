from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from app.change_context_artifacts import ChangeContextArtifactStore


class ChangeContextArtifactStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "artifacts"
        self.store = ChangeContextArtifactStore(self.root)

    def test_root_and_files_are_private_content_addressed_and_verified(self) -> None:
        payload = {"facts": {"project": "his"}, "missing": [], "conflicts": []}
        record = self.store.persist_layer(payload)

        self.assertEqual(0o700, stat.S_IMODE(self.root.stat().st_mode))
        layer_path = self.store.path_for(record.content_hash)
        self.assertEqual(0o600, stat.S_IMODE(layer_path.stat().st_mode))
        self.assertEqual(1, layer_path.stat().st_nlink)
        self.assertEqual(payload, self.store.reopen(record))
        self.assertTrue((layer_path.parent / "seal.json").is_file())
        self.assertEqual(
            Path("sha256") / record.content_hash[7:9] / record.content_hash[9:] / "layer.json",
            layer_path.relative_to(self.root),
        )

    def test_root_must_be_absolute_and_not_a_symlink(self) -> None:
        with self.assertRaisesRegex(ValueError, "change_context_artifact_root_invalid"):
            ChangeContextArtifactStore(Path("relative"))
        real = Path(self.temporary.name) / "real"
        real.mkdir()
        link = Path(self.temporary.name) / "link"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "change_context_artifact_root_invalid"):
            ChangeContextArtifactStore(link)

    def test_duplicate_and_oversize_payloads_are_rejected(self) -> None:
        payload = {"facts": {"project": "his"}, "missing": [], "conflicts": []}
        self.store.persist_layer(payload)
        with self.assertRaisesRegex(ValueError, "change_context_artifact_exists"):
            self.store.persist_layer(payload)
        with self.assertRaisesRegex(ValueError, "change_context_artifact_limit_exceeded"):
            self.store.persist_bytes(b"x" * (8 * 1024 * 1024 + 1))

    def test_tamper_symlink_and_hardlink_are_rejected_on_reopen(self) -> None:
        payload = {"facts": {"project": "his"}, "missing": [], "conflicts": []}
        record = self.store.persist_layer(payload)
        path = self.store.path_for(record.content_hash)
        path.write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "change_context_artifact_(changed|hash_mismatch)"):
            self.store.reopen(record)

        other_payload = {"facts": {"project": "other"}, "missing": [], "conflicts": []}
        other = self.store.persist_layer(other_payload)
        other_path = self.store.path_for(other.content_hash)
        hardlink = other_path.with_name("hardlink.json")
        os.link(other_path, hardlink)
        with self.assertRaisesRegex(ValueError, "change_context_artifact_link_invalid"):
            self.store.reopen(other)

    def test_record_round_trip_is_strict(self) -> None:
        record = self.store.persist_layer({"facts": {}, "missing": [], "conflicts": []})
        self.assertEqual(record, type(record).from_dict(record.to_dict()))
        malformed = record.to_dict()
        malformed["extra"] = True
        with self.assertRaises(ValueError):
            type(record).from_dict(malformed)


if __name__ == "__main__":
    unittest.main()

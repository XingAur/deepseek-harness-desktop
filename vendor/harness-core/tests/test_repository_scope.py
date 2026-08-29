from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.repository_scope import RepositoryScope


class RepositoryScopeTests(unittest.TestCase):
    def test_configured_absolute_existing_root_resolves_only_contained_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            source = root / "src"
            source.mkdir(parents=True)
            (source / "safe.txt").write_text("safe\n", encoding="utf-8")
            scope = RepositoryScope("local-repo", root, allowed_paths=("src",))

            self.assertEqual(root.resolve(), scope.root)
            self.assertEqual((source / "safe.txt").resolve(), scope.resolve_path("src/safe.txt"))
            with self.assertRaisesRegex(ValueError, "repository_scope_path_not_allowed"):
                scope.resolve_path("README.md")
            for unsafe in ("../outside.txt", "/tmp/outside.txt", "src/../README.md"):
                with self.subTest(path=unsafe):
                    with self.assertRaisesRegex(ValueError, "repository_scope_path_invalid"):
                        scope.resolve_path(unsafe)

    def test_scope_rejects_symlink_escape_before_a_git_command_can_use_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            outside = Path(directory) / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("not-in-repository", encoding="utf-8")
            link = root / "linked"
            try:
                os.symlink(outside, link)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            scope = RepositoryScope("local-repo", root, allowed_paths=(".",))

            with self.assertRaisesRegex(ValueError, "repository_scope_symlink_escape"):
                scope.resolve_path("linked/secret.txt")

    def test_scope_rejects_missing_relative_or_non_absolute_configured_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "repository_scope_root_invalid"):
                RepositoryScope("local-repo", Path("relative-repo"), allowed_paths=(".",))
            with self.assertRaisesRegex(ValueError, "repository_scope_root_invalid"):
                RepositoryScope("local-repo", Path(directory) / "missing", allowed_paths=(".",))

    def test_default_https_port_is_canonicalized_to_the_same_remote_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            scope = RepositoryScope(
                "local-repo",
                root,
                allowed_paths=(".",),
                remotes=(("origin", "https://gitlab.example.test:443/group/project.git"),),
            )

            self.assertEqual(
                "https://gitlab.example.test/group/project.git",
                scope.remote_url("origin"),
            )


if __name__ == "__main__":
    unittest.main()

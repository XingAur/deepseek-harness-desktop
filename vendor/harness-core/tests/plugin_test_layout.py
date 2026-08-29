from __future__ import annotations

from pathlib import Path

from app.plugin_inventory import resolve_plugin_source_root


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FORMAL_PLUGIN_ROOT = Path("/Users/lym/plugins")
PLUGIN_SOURCE_ROOT = resolve_plugin_source_root(
    REPOSITORY_ROOT,
    FORMAL_PLUGIN_ROOT,
)

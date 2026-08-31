from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.contract_plugins import (
    DEFAULT_CONTRACT_PLUGIN_PACK,
    apply_contract_plugins,
    load_contract_plugin_pack,
)


class ContractPluginTests(unittest.TestCase):
    def test_default_pack_matches_versioned_parameter_and_scope_rules(self) -> None:
        pack = load_contract_plugin_pack(DEFAULT_CONTRACT_PLUGIN_PACK)

        matches = apply_contract_plugins(
            "菜单/路由参数 paiBanMs：1 只保留医生为空排班，2 只保留有医生排班，空值保持默认模式。",
            pack=pack,
            user_overrides=True,
        )

        self.assertEqual(["dfhis.schedule-mode-route-param"], [item["plugin_id"] for item in matches])
        parameter = matches[0]["parameters"][0]
        self.assertEqual("paiBanMs", parameter["name"])
        self.assertEqual("route_menu_param", parameter["location"])
        self.assertEqual("user_instruction", parameter["source"])
        self.assertEqual("只过滤医生为空的排班", parameter["allowed_values"]["1"])
        self.assertIn("空值", parameter["allowed_values"]["empty"])
        self.assertIn("不按需求图", matches[0]["scope"]["do_not"][0])

    def test_default_pack_keeps_conditional_sort_default_explicit(self) -> None:
        pack = load_contract_plugin_pack(DEFAULT_CONTRACT_PLUGIN_PACK)

        without_default = apply_contract_plugins("接口新增 sortField=排序字段。", pack=pack)
        with_default = apply_contract_plugins("接口新增 sortField=排序字段，未设置排序保持默认排序。", pack=pack)

        self.assertNotIn("empty", without_default[0]["parameters"][0]["allowed_values"])
        self.assertIn("empty", with_default[0]["parameters"][0]["allowed_values"])

    def test_invalid_pack_is_rejected_before_matching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid.json"
            path.write_text(json.dumps({"schema_version": "wrong", "plugins": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_contract_plugin_pack(path)

    def test_plugins_are_data_driven_without_ticket_ids(self) -> None:
        payload = json.loads(DEFAULT_CONTRACT_PLUGIN_PACK.read_text(encoding="utf-8"))
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertNotIn("DFHIS-", serialized)
        self.assertEqual("1.0-contract-plugin-pack", payload["schema_version"])
        self.assertTrue(all(item.get("id") and item.get("version") for item in payload["plugins"]))


if __name__ == "__main__":
    unittest.main()

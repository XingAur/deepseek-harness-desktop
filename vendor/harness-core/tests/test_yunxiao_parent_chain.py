from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.requirement_archive import archive_yunxiao_requirement
from app.yunxiao_read import collect_yunxiao_evidence


class _Credentials:
    ok = True
    missing_keys: list[str] = []

    def safe_summary(self) -> dict[str, str]:
        return {"pat": "present", "organization_id": "present"}


class _FakeYunxiaoClient:
    def __init__(self, staging_root: Path) -> None:
        self.staging_root = staging_root
        self.work_items = {
            "DFHIS-32178": {
                "id": "child-id",
                "serialNumber": "DFHIS-32178",
                "subject": "线上医保退费",
                "description": {"htmlValue": "HIS 退费完成后调用线上医保退款。"},
                "parentId": "parent-id",
            },
            "parent-id": {
                "id": "parent-id",
                "serialNumber": "ORIGIN-10910",
                "subject": "医保移动支付接入方案",
                "description": {"htmlValue": "父需求定义 Q104 医保退费接口。"},
            },
        }

    def get_work_item_info(self, work_item_id: str) -> dict:
        item = self.work_items.get(work_item_id)
        return {
            "ok": item is not None,
            "data": item or {},
            "attempts": [{"label": "GetWorkItemInfo", "status": "success"}],
        }

    def list_comments(self, work_item_id: str) -> dict:
        return {
            "ok": True,
            "data": [],
            "attempts": [{"label": "ListWorkitemComments", "status": "success"}],
        }

    def list_attachments(self, work_item_id: str) -> dict:
        attachments = (
            [{
                "fileId": "parent-docx",
                "fileName": "医保移动支付接入方案.docx",
                "fileSize": 5,
                "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }]
            if work_item_id == "parent-id"
            else []
        )
        return {
            "ok": True,
            "data": attachments,
            "attempts": [{"label": "ListWorkitemAttachments", "status": "success"}],
        }

    def collect_file_details(
        self,
        work_item_id: str,
        files: list[dict],
        output_dir: Path | None = None,
        max_files: int | None = 10,
        max_download_bytes: int | None = None,
    ) -> dict:
        items = []
        attempts = []
        for file_ref in files:
            identifier = str(file_ref.get("identifier") or "")
            if not identifier:
                continue
            path = ""
            download = {}
            if output_dir is not None:
                path_obj = output_dir / "yunxiao_inline_files" / f"{identifier}.docx"
                path_obj.parent.mkdir(parents=True, exist_ok=True)
                path_obj.write_bytes(b"docx")
                path = str(path_obj)
                download = {
                    "identifier": identifier,
                    "name": file_ref.get("name") or "",
                    "kind": "attachment",
                    "status": "success",
                    "path": path,
                    "size": 4,
                    "sha256": hashlib.sha256(b"docx").hexdigest(),
                    "content_type": file_ref.get("content_type") or "",
                }
            items.append({
                "identifier": identifier,
                "name": file_ref.get("name") or "",
                "kind": "attachment",
                "status": "success",
                "download": download,
            })
            attempts.append({"label": "GetWorkitemFile", "status": "success"})
        return {"items": items, "attempts": attempts}

    def download_inline_files(self, **kwargs: object) -> dict:
        return {"items": [], "attempts": []}


class YunxiaoParentChainTests(unittest.TestCase):
    def test_collects_parent_work_item_and_its_attachment_before_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            staging = Path(temp_dir)
            client = _FakeYunxiaoClient(staging)
            with patch("app.yunxiao_read.load_yunxiao_credentials", return_value=_Credentials()), patch(
                "app.yunxiao_read.YunxiaoReadClient", return_value=client
            ):
                evidence = collect_yunxiao_evidence(
                    yunxiao_url="https://devops.aliyun.com/projex/req/DFHIS-32178",
                    demand_text="DFHIS-32178",
                    output_dir=staging,
                    download_policy="archive",
                )

            self.assertEqual("success", evidence["status"])
            self.assertEqual("complete", evidence["parent_chain"]["status"])
            self.assertEqual("ORIGIN-10910", evidence["parent_work_items"][0]["serial_number"])
            self.assertEqual(1, len(evidence["parent_work_items"][0]["attachments"]))
            self.assertEqual(1, len(evidence["attachments"]))
            self.assertEqual("ORIGIN-10910", evidence["attachments"][0]["source_work_item_serial_number"])
            self.assertEqual("success", evidence["inline_file_downloads"][0]["status"])

    def test_parent_attachment_is_preserved_in_the_ticket_archive_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "staging"
            client = _FakeYunxiaoClient(staging)
            with patch("app.yunxiao_read.load_yunxiao_credentials", return_value=_Credentials()), patch(
                "app.yunxiao_read.YunxiaoReadClient", return_value=client
            ):
                evidence = collect_yunxiao_evidence(
                    yunxiao_url="https://devops.aliyun.com/projex/req/DFHIS-32178",
                    demand_text="DFHIS-32178",
                    output_dir=staging,
                    download_policy="archive",
                )
            archive = archive_yunxiao_requirement(
                archive_root=root / "archive",
                evidence=evidence,
                media_staging_root=staging,
            )
            manifest = __import__("json").loads(Path(archive["manifest_path"]).read_text(encoding="utf-8"))

            self.assertEqual("complete", manifest["status"])
            self.assertEqual("ORIGIN-10910", manifest["items"][0]["source_work_item_serial_number"])
            self.assertTrue(
                (Path(archive["ticket_dir"]) / "yunxiao" / manifest["items"][0]["stored_path"]).is_file()
            )


if __name__ == "__main__":
    unittest.main()

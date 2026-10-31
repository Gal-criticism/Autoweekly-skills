import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
import zipfile

from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "weekly-outro/scripts/runtime"))

from outro import roster


def response(payload, status=200):
    return roster.TransportResponse(
        status=status,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


class FakeFeishu:
    def __init__(self, field_pages, record_pages, *, token_pages=None, node=None):
        self.field_pages = list(field_pages)
        self.record_pages = list(record_pages)
        self.token_pages = list(token_pages or [{"code": 0, "tenant_access_token": "t-test", "expire": 7200}])
        self.node = node or {
            "code": 0,
            "data": {
                "node": {
                    "node_token": "wikcnRoster",
                    "obj_type": "bitable",
                    "obj_token": "bascnRoster",
                    "title": "Roster",
                }
            },
        }
        self.calls = []

    def __call__(self, method, url, headers, body):
        parsed = urlsplit(url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        self.calls.append(
            {
                "method": method,
                "path": parsed.path,
                "query": query,
                "headers": dict(headers),
                "body": body,
            }
        )
        if parsed.path == roster.TENANT_TOKEN_PATH:
            return response(self.token_pages.pop(0))
        if parsed.path == roster.WIKI_NODE_PATH:
            return response(self.node)
        if parsed.path.endswith("/fields"):
            return response(self.field_pages.pop(0))
        if parsed.path.endswith("/records"):
            return response(self.record_pages.pop(0))
        raise AssertionError(f"unexpected path {parsed.path}")


def page(items, *, more=False, token=None):
    data = {"items": items, "has_more": more}
    if token is not None:
        data["page_token"] = token
    return {"code": 0, "data": data}


def fields_and_records(*, target_roles=None):
    target_roles = target_roles or {
        "writer": [
            {"id": "ou_writer_a", "name": "Alice"},
            {"open_id": "ou_writer_a", "en_name": "Alice duplicate"},
            {"id": "ou_writer_b", "name": "Bob"},
        ],
        "reviewer": [
            {"id": "ou_writer_a", "name": "Alice"},
            {"id": "ou_reviewer_b", "name": "Carol\nLine two"},
        ],
    }
    field_pages = [
        page(
            [
                {
                    "field_id": "fldIssue",
                    "field_name": "Issue",
                    "type": 1,
                    "ui_type": "Text",
                },
                {
                    "field_id": "fldWriter",
                    "field_name": "Writer",
                    "type": 11,
                    "ui_type": "User",
                },
            ],
            more=True,
            token="field-next",
        ),
        page(
            [
                {
                    "field_id": "fldReviewer",
                    "field_name": "Reviewer",
                    "type": 11,
                    "ui_type": "User",
                }
            ]
        ),
    ]
    record_pages = [
        page(
            [
                {
                    "record_id": "recOther",
                    "fields": {
                        "Issue": [{"type": "text", "text": "Issue 2400"}],
                    },
                }
            ],
            more=True,
            token="record-two",
        ),
        page(
            [
                {
                    "record_id": "recTarget",
                    "fields": {
                        "fldIssue": [
                            {"type": "text", "text": "Issue "},
                            {"type": "text", "text": "240"},
                        ],
                        "fldWriter": target_roles["writer"],
                        "fldReviewer": target_roles["reviewer"],
                    },
                }
            ],
            more=True,
            token="record-three",
        ),
        page(
            [
                {
                    "record_id": "recLater",
                    "fields": {
                        "fldIssue": [{"type": "text", "text": "241"}],
                    },
                }
            ]
        ),
    ]
    return field_pages, record_pages


class FeishuRosterTests(unittest.TestCase):
    def test_view_pagination_exact_issue_roles_and_schema_binding(self):
        field_pages, record_pages = fields_and_records()
        transport = FakeFeishu(field_pages, record_pages)
        client = roster.FeishuClient(
            roster.FeishuCredentials(user_access_token="user-token-for-test"),
            transport=transport,
            clock=lambda: 1000.0,
        )

        result = roster.build_roster(
            client=client,
            wiki_url_or_token="https://example.feishu.cn/wiki/wikcnRoster?from=clipboard",
            table_id="tblRoster",
            view_id="vewIssue240",
            issue_field_id="fldIssue",
            issue="Issue 240",
            roles={"writer": "fldWriter", "reviewer": "fldReviewer"},
            page_size=2,
        )

        self.assertEqual(result["record_id"], "recTarget")
        self.assertEqual([role["role"] for role in result["roles"]], ["writer", "reviewer"])
        self.assertEqual(
            [member["display"] for member in result["roles"][0]["members"]],
            ["Alice", "Bob"],
        )
        self.assertEqual(
            [member["display"] for member in result["roles"][1]["members"]],
            ["Alice", "Carol\nLine two"],
        )
        self.assertEqual(len(result["schema_sha256"]), 64)
        record_calls = [
            call for call in transport.calls
            if call["path"].endswith("/records")
        ]
        self.assertEqual([call["query"].get("page_token") for call in record_calls], [None, "record-two", "record-three"])
        self.assertTrue(all(call["query"]["view_id"] == "vewIssue240" for call in record_calls))

        rebound = roster.bind_schema(
            [
                {
                    "field_id": "fldWriter",
                    "field_name": "Writer",
                    "type": 11,
                    "ui_type": "User",
                },
                {
                    "field_id": "fldIssue",
                    "field_name": "Issue",
                    "type": 1,
                    "ui_type": "Text",
                },
                {
                    "field_id": "fldReviewer",
                    "field_name": "Reviewer",
                    "type": 11,
                    "ui_type": "User",
                },
            ],
            table_id="tblRoster",
            view_id="vewIssue240",
            issue_field_id="fldIssue",
            roles={"writer": "fldWriter", "reviewer": "fldReviewer"},
            expected_schema_sha256=result["schema_sha256"],
        )
        self.assertEqual(rebound["sha256"], result["schema_sha256"])

    def test_tenant_token_refreshes_once_on_auth_error(self):
        class RefreshTransport(FakeFeishu):
            def __init__(self):
                super().__init__(
                    [],
                    [],
                    token_pages=[
                        {"code": 0, "tenant_access_token": "tenant-one", "expire": 7200},
                        {"code": 0, "tenant_access_token": "tenant-two", "expire": 7200},
                    ],
                )
                self.wiki_attempts = 0

            def __call__(self, method, url, headers, body):
                if urlsplit(url).path == roster.WIKI_NODE_PATH:
                    self.wiki_attempts += 1
                    if self.wiki_attempts == 1:
                        parsed = urlsplit(url)
                        query = {
                            key: values[0]
                            for key, values in parse_qs(parsed.query).items()
                        }
                        self.calls.append(
                            {
                                "method": method,
                                "path": parsed.path,
                                "query": query,
                                "headers": dict(headers),
                                "body": body,
                            }
                        )
                        return response({"code": 99991663, "msg": "expired"})
                return super().__call__(method, url, headers, body)

        transport = RefreshTransport()
        credentials = roster.FeishuCredentials(
            app_id="cli_test",
            app_secret="secret-never-log",
        )
        client = roster.FeishuClient(credentials, transport=transport, clock=lambda: 1000.0)
        resolved = roster.resolve_wiki_bitable(client, "wikcnRoster")

        self.assertEqual(resolved["app_token"], "bascnRoster")
        token_calls = [
            call for call in transport.calls
            if call["path"] == roster.TENANT_TOKEN_PATH
        ]
        self.assertEqual(len(token_calls), 2)
        wiki_calls = [
            call for call in transport.calls
            if call["path"] == roster.WIKI_NODE_PATH
        ]
        self.assertEqual(wiki_calls[0]["headers"]["Authorization"], "Bearer tenant-one")
        self.assertEqual(wiki_calls[1]["headers"]["Authorization"], "Bearer tenant-two")
        self.assertNotIn("secret-never-log", repr(credentials))
        self.assertNotIn("tenant-one", repr(client))

    def test_api_errors_and_missing_roles_are_clear_and_secret_safe(self):
        credentials = roster.FeishuCredentials(user_access_token="user-secret-token")
        client = roster.FeishuClient(credentials, transport=lambda *args: response({"code": 0, "data": {}}))
        with self.assertRaises(roster.FeishuResponseError):
            roster.resolve_wiki_bitable(client, "wikcnRoster")

        bad_secret_transport = lambda *args: roster.TransportResponse(
            status=500,
            body=json.dumps(
                {"code": 123, "msg": "failed user-secret-token"},
            ).encode(),
        )
        bad_client = roster.FeishuClient(credentials, transport=bad_secret_transport)
        with self.assertRaises(roster.FeishuAPIError) as caught:
            roster.resolve_wiki_bitable(bad_client, "wikcnRoster")
        self.assertNotIn("user-secret-token", str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

        field_pages, record_pages = fields_and_records(
            target_roles={
                "writer": [{"id": "ou_writer_a", "name": "Alice"}],
                "reviewer": [],
            }
        )
        empty_client = roster.FeishuClient(
            credentials,
            transport=FakeFeishu(field_pages, record_pages),
            clock=lambda: 1000.0,
        )
        with self.assertRaisesRegex(roster.EmptyRoleError, "reviewer"):
            roster.build_roster(
                client=empty_client,
                wiki_url_or_token="wikcnRoster",
                table_id="tblRoster",
                view_id="vewIssue240",
                issue_field_id="fldIssue",
                issue="Issue 240",
                roles={"writer": "fldWriter", "reviewer": "fldReviewer"},
                page_size=2,
            )

    def test_empty_and_duplicate_issue_selections_fail(self):
        fields = [
            {
                "field_id": "fldIssue",
                "field_name": "Issue",
                "type": 1,
                "ui_type": "Text",
            },
            {
                "field_id": "fldWriter",
                "field_name": "Writer",
                "type": 11,
                "ui_type": "User",
            },
        ]
        roles = {"writer": "fldWriter"}
        empty_transport = FakeFeishu(
            [page(fields)],
            [page([])],
        )
        client = roster.FeishuClient(
            roster.FeishuCredentials(user_access_token="test"),
            transport=empty_transport,
            clock=lambda: 1000.0,
        )
        with self.assertRaises(roster.IssueNotFoundError):
            roster.build_roster(
                client=client,
                wiki_url_or_token="wikcnRoster",
                table_id="tblRoster",
                view_id="vew",
                issue_field_id="fldIssue",
                issue="240",
                roles=roles,
            )

        duplicate_records = page(
            [
                {
                    "record_id": "recOne",
                    "fields": {"fldIssue": "240", "fldWriter": [{"id": "a", "name": "A"}]},
                },
                {
                    "record_id": "recTwo",
                    "fields": {"fldIssue": "240", "fldWriter": [{"id": "b", "name": "B"}]},
                },
            ]
        )
        duplicate_transport = FakeFeishu([page(fields)], [duplicate_records])
        duplicate_client = roster.FeishuClient(
            roster.FeishuCredentials(user_access_token="test"),
            transport=duplicate_transport,
            clock=lambda: 1000.0,
        )
        with self.assertRaises(roster.IssueNotUniqueError):
            roster.build_roster(
                client=duplicate_client,
                wiki_url_or_token="wikcnRoster",
                table_id="tblRoster",
                view_id="vew",
                issue_field_id="fldIssue",
                issue=240,
                roles=roles,
            )


def force_stale_dimension(path):
    with zipfile.ZipFile(path, "r") as source:
        members = [(item, source.read(item.filename)) for item in source.infolist()]
    with zipfile.ZipFile(path, "w") as target:
        for item, data in members:
            if item.filename == "xl/worksheets/sheet1.xml":
                data, count = re.subn(
                    rb'<dimension ref="[^"]+"/>',
                    b'<dimension ref="A1"/>',
                    data,
                    count=1,
                )
                if count != 1:
                    raise AssertionError("worksheet dimension was not found")
            target.writestr(item, data)


class XlsxRosterTests(unittest.TestCase):
    def make_workbook(self, root):
        path = Path(root) / "roster.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "周报"
        sheet.append(["期数", "配音", "审核名单", "备注"])
        sheet.append([250, "果老师ABC\n第二行", "审核甲,审核乙", "keep"])
        sheet.append([251, "其他", "其他审核", ""])
        workbook.save(path)
        workbook.close()
        force_stale_dimension(path)
        return path

    def test_inspect_resets_stale_dimension_and_snapshot_preserves_multiline_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_workbook(directory)
            inspection = roster.inspect_workbook(path)
            sheet = inspection["sheets"][0]
            self.assertGreater(sheet["row_count"], 1)
            self.assertEqual(sheet["rows"][1]["values"][1], "果老师ABC\n第二行")

            snapshot = roster.build_xlsx_roster_snapshot(
                path,
                sheet_name="周报",
                issue_column="期数",
                issue=250,
                roles={"配音": "配音", "审核": "审核名单"},
            )
            self.assertEqual(snapshot["row_number"], 2)
            self.assertEqual(
                snapshot["roles"][0]["members"],
                [{"identity": "果老师ABC\n第二行", "display": "果老师ABC\n第二行"}],
            )
            self.assertEqual(
                snapshot["roles"][1]["members"],
                [{"identity": "审核甲,审核乙", "display": "审核甲,审核乙"}],
            )
            self.assertEqual(len(snapshot["schema_sha256"]), 64)
            self.assertEqual(
                roster.build_xlsx_roster_snapshot(
                    path,
                    sheet_name="周报",
                    issue_column="期数",
                    issue="250",
                    roles={"配音": "配音", "审核": "审核名单"},
                )["schema_sha256"],
                snapshot["schema_sha256"],
            )

    def test_xlsx_rejects_duplicate_issue_rows_empty_roles_and_nonzip_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_workbook(directory)

            from openpyxl import load_workbook
            workbook = load_workbook(path)
            worksheet = workbook["周报"]
            worksheet.append([250, "重复", "重复审核", ""])
            workbook.save(path)
            workbook.close()
            with self.assertRaises(roster.IssueNotUniqueError):
                roster.build_xlsx_roster_snapshot(
                    path,
                    sheet_name="周报",
                    issue_column="期数",
                    issue=250,
                    roles={"配音": "配音"},
                )

            empty_path = Path(directory) / "empty-role.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "周报"
            worksheet.append(["期数", "配音"])
            worksheet.append([250, "   \n  "])
            workbook.save(empty_path)
            workbook.close()
            with self.assertRaisesRegex(roster.EmptyRoleError, "配音"):
                roster.build_xlsx_roster_snapshot(
                    empty_path,
                    sheet_name="周报",
                    issue_column="期数",
                    issue=250,
                    roles={"配音": "配音"},
                )

            invalid = Path(directory) / "invalid.xlsx"
            invalid.write_bytes(b"not a zip")
            with self.assertRaises(roster.WorkbookError):
                roster.inspect_workbook(invalid)

    def test_issue250_row19_uses_only_explicit_column_separators(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "issue250.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "周报工作表"
            worksheet.append(
                [
                    "期数",
                    "新闻采集",
                    "文案",
                    "素材汇总",
                    "配音",
                    "修音",
                    "制作辅助",
                    "后期",
                    "时轴",
                    "轴校",
                ]
            )
            for _ in range(17):
                worksheet.append([None] * 10)
            worksheet.append(
                [
                    250,
                    "Zinogre、向澜秋、黑田、Langley.、Hsp63、路陌之深",
                    "Zinogre、剩斗士星星、EnMa0、kksk、Casillas、式轩",
                    "弦叶 石竹 夜雨 Capercaillie",
                    "甲\n乙",
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            workbook.save(path)
            workbook.close()

            snapshot = roster.build_xlsx_roster_snapshot(
                path,
                sheet_name="周报工作表",
                issue_column="期数",
                issue=250,
                roles={
                    "news": "新闻采集",
                    "writer": "文案",
                    "material": "素材汇总",
                    "voice": "配音",
                },
                member_separators={
                    "新闻采集": ["、"],
                    "writer": ["、"],
                    "素材汇总": [" "],
                },
            )

            self.assertEqual(snapshot["row_number"], 19)
            members = {
                role["role"]: [member["display"] for member in role["members"]]
                for role in snapshot["roles"]
            }
            self.assertEqual(
                members["news"],
                ["Zinogre", "向澜秋", "黑田", "Langley.", "Hsp63", "路陌之深"],
            )
            self.assertEqual(
                members["writer"],
                ["Zinogre", "剩斗士星星", "EnMa0", "kksk", "Casillas", "式轩"],
            )
            self.assertEqual(
                members["material"],
                ["弦叶", "石竹", "夜雨", "Capercaillie"],
            )
            self.assertEqual(members["voice"], ["甲\n乙"])
            self.assertEqual(
                snapshot["schema"]["roles"][3]["separators"],
                [],
            )


if __name__ == "__main__":
    unittest.main()

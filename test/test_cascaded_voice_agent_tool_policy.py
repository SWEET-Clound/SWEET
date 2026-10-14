"""VoiceAgent 工具白名单的独立安全边界测试。"""

import unittest
from dataclasses import dataclass

from qll_chat.cascaded_voice_agent.tool_policy import filter_voice_tools


@dataclass
class _ToolMeta:
    raw_name: str = ""


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name}}


class VoiceToolPolicyTest(unittest.TestCase):
    def test_only_search_and_read_are_kept(self) -> None:
        schemas = [
            _schema("search_safe"),
            _schema("read_safe"),
            _schema("write_safe"),
            _schema("unknown_safe"),
        ]
        registry = {
            "search_safe": _ToolMeta("search_weisou"),
            "read_safe": _ToolMeta("read_weisou_document"),
            "write_safe": _ToolMeta("write_document"),
        }

        filtered_schemas, filtered_registry = filter_voice_tools(
            {}, schemas, registry
        )

        self.assertEqual(
            [item["function"]["name"] for item in filtered_schemas],
            ["search_safe", "read_safe"],
        )
        self.assertEqual(
            set(filtered_registry),
            {"search_safe", "read_safe"},
        )

    def test_safe_to_raw_mapping_is_used_when_metadata_has_no_raw_name(self) -> None:
        req = {
            "tool_name_map": {
                "safe_to_raw": {"search_weisou_ab12": "search_weisou"}
            }
        }
        registry = {"search_weisou_ab12": _ToolMeta()}

        filtered_schemas, filtered_registry = filter_voice_tools(
            req,
            [_schema("search_weisou_ab12")],
            registry,
        )

        self.assertEqual(filtered_schemas, [_schema("search_weisou_ab12")])
        self.assertEqual(filtered_registry, registry)

    def test_schema_without_executable_registry_entry_is_rejected(self) -> None:
        req = {
            "tool_name_map": {
                "safe_to_raw": {"search_weisou_ab12": "search_weisou"}
            }
        }

        filtered_schemas, filtered_registry = filter_voice_tools(
            req,
            [_schema("search_weisou_ab12")],
            {},
        )

        self.assertEqual(filtered_schemas, [])
        self.assertEqual(filtered_registry, {})

    def test_malformed_name_mapping_fails_closed(self) -> None:
        filtered_schemas, filtered_registry = filter_voice_tools(
            {"tool_name_map": {"safe_to_raw": ["search_weisou"]}},
            [_schema("search_safe")],
            {"search_safe": _ToolMeta()},
        )

        self.assertEqual(filtered_schemas, [])
        self.assertEqual(filtered_registry, {})


if __name__ == "__main__":
    unittest.main()

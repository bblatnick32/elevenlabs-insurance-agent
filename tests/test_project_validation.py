import json
import shutil
import tempfile
import unittest
from pathlib import Path

from verify.project_validation import project_failures

ROOT = Path(__file__).resolve().parents[1]


class ProjectValidationTests(unittest.TestCase):
    def test_checked_in_project_is_consistent(self) -> None:
        self.assertEqual(project_failures(ROOT), ())

    def test_rejects_tool_and_attached_test_drift(self) -> None:
        with self._copy_project_config() as copied:
            root = Path(copied)
            tool_path = root / "tool_configs" / "lookup_policy.json"
            tool = json.loads(tool_path.read_text())
            tool["parameters"]["properties"]["policy_number"]["type"] = "integer"
            tool_path.write_text(json.dumps(tool))

            agent_path = (
                root / "agent_configs" / "policy-servicing-vehicle-addition-agent.json"
            )
            agent = json.loads(agent_path.read_text())
            agent["conversation_config"]["agent"]["prompt"]["tool_ids"].reverse()
            agent["platform_settings"]["testing"]["attached_tests"].pop()
            agent_path.write_text(json.dumps(agent))

            failures = project_failures(root)

        details = tuple(failure.detail for failure in failures)
        self.assertTrue(any("/type: must be string" in detail for detail in details))
        self.assertTrue(any("tool_ids" in detail for detail in details))
        self.assertTrue(any("attached_tests" in detail for detail in details))

    def _copy_project_config(self) -> tempfile.TemporaryDirectory[str]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        shutil.copy2(ROOT / "tools.json", root / "tools.json")
        shutil.copy2(ROOT / "tests.json", root / "tests.json")
        shutil.copytree(ROOT / "tool_configs", root / "tool_configs")
        shutil.copytree(ROOT / "test_configs", root / "test_configs")
        shutil.copytree(ROOT / "agent_configs", root / "agent_configs")
        return temporary


if __name__ == "__main__":
    unittest.main()

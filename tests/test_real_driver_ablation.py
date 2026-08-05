#!/usr/bin/env python3

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts" / "real_driver_ablation.py"
MOCK = ROOT / "tests" / "fixtures" / "real_driver_mock_agent.py"


class RealDriverAblationTest(unittest.TestCase):
    def test_real_driver_runs_both_arms_and_resolves_model_from_runtime_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = root / "cases.json"
            cases.write_text(
                json.dumps(
                    [
                        {
                            "id": "positive",
                            "prompt": "Create current Python Interactions code.",
                            "should_trigger": True,
                            "language": "python",
                            "expected_checks": [
                                "from\\s+google\\s+import\\s+genai",
                                "genai\\.Client\\(",
                                "\\.interactions\\.create\\(",
                                "FORBID:\\.start_chat\\(",
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            artifacts = root / "artifacts"
            sessions = root / "sessions"
            command = f"{sys.executable} {MOCK} {{task}} {{session_root}}"
            result = subprocess.run(
                [
                    sys.executable,
                    str(DRIVER),
                    "--cases",
                    str(cases),
                    "--skill",
                    str(ROOT / "skills" / "gemini_interactions" / "skills.md"),
                    "--agent-cmd",
                    command,
                    "--session-root",
                    str(sessions),
                    "--runs",
                    "2",
                    "--workers",
                    "2",
                    "--artifacts",
                    str(artifacts),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["schema_version"], "real-driver-ablation@0.2.0")
            self.assertEqual(report["telemetry"]["with_skill_success_rate"], 1.0)
            self.assertEqual(report["telemetry"]["without_skill_success_rate"], 0.0)
            self.assertEqual(report["telemetry"]["delta"], 1.0)
            self.assertEqual(report["runtime"]["resolved_models"], ["fixture-model"])
            self.assertEqual(report["runtime"]["model_source"], "runtime-session-metadata")
            self.assertTrue((artifacts / "with_skill" / "positive" / "run-0.json").is_file())
            self.assertTrue((artifacts / "with_skill" / "positive" / "run-1.json").is_file())
            self.assertTrue((artifacts / "without_skill" / "positive" / "run-0.json").is_file())
            self.assertTrue((artifacts / "without_skill" / "positive" / "run-1.json").is_file())

            saved_threads = {
                path.relative_to(artifacts).as_posix(): json.loads(path.read_text(encoding="utf-8"))["thread_id"]
                for path in artifacts.glob("**/run-*.json")
            }
            resumed = subprocess.run(result.args + ["--resume"], cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            self.assertEqual(
                saved_threads,
                {
                    path.relative_to(artifacts).as_posix(): json.loads(path.read_text(encoding="utf-8"))["thread_id"]
                    for path in artifacts.glob("**/run-*.json")
                },
            )
            self.assertIn("resumed=True", resumed.stderr)

            stale_path = artifacts / "with_skill" / "positive" / "run-0.json"
            stale = json.loads(stale_path.read_text(encoding="utf-8"))
            stale["cwd_kind"] = "shared-repository"
            stale_path.write_text(json.dumps(stale), encoding="utf-8")
            rejected = subprocess.run(result.args + ["--resume"], cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)
            self.assertIn("cwd_kind", rejected.stderr)

    def test_agent_command_must_contain_task_placeholder(self) -> None:
        result = subprocess.run(
            [sys.executable, str(DRIVER), "--agent-cmd", "false"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("{task}", result.stderr)

    def test_codex_command_requires_skip_git_repo_check_for_ephemeral_cwd(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(DRIVER),
                "--agent-cmd",
                "/definitely-missing/codex exec {task}",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--skip-git-repo-check", result.stderr)

    def test_nonzero_agent_exit_can_never_produce_a_passing_ablation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(DRIVER),
                    "--agent-cmd",
                    "false {task}",
                    "--runs",
                    "1",
                    "--threshold",
                    "0",
                    "--artifacts",
                    str(Path(tmp) / "artifacts"),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 3)
            report = json.loads(result.stdout)
            self.assertGreater(report["telemetry"]["agent_failures"], 0)
            self.assertEqual(report["telemetry"]["verdict"], "FAIL")

    def test_agent_timeout_is_recorded_as_failure_instead_of_crashing_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(DRIVER),
                    "--agent-cmd",
                    f"{sys.executable} {MOCK} {{task}} {{session_root}}",
                    "--session-root",
                    str(Path(tmp) / "sessions"),
                    "--runs",
                    "1",
                    "--workers",
                    "2",
                    "--timeout",
                    "0",
                    "--artifacts",
                    str(Path(tmp) / "artifacts"),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            report = json.loads(result.stdout)
            self.assertGreater(report["telemetry"]["agent_failures"], 0)
            self.assertEqual(report["telemetry"]["verdict"], "FAIL")

    def test_timed_out_partial_output_cannot_count_as_case_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = root / "cases.json"
            cases.write_text(
                json.dumps(
                    [
                        {
                            "id": "partial-timeout",
                            "prompt": "OUTPUT_THEN_SLEEP",
                            "expected_checks": ["EXPECTED_TOKEN"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            artifacts = root / "artifacts"
            result = subprocess.run(
                [
                    sys.executable,
                    str(DRIVER),
                    "--cases",
                    str(cases),
                    "--agent-cmd",
                    f"{sys.executable} {MOCK} {{task}} {{session_root}}",
                    "--session-root",
                    str(root / "sessions"),
                    "--runs",
                    "1",
                    "--workers",
                    "2",
                    "--timeout",
                    "1",
                    "--artifacts",
                    str(artifacts),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            timed_out = json.loads(
                (artifacts / "with_skill" / "partial-timeout" / "run-0.json").read_text(encoding="utf-8")
            )
            self.assertEqual(timed_out["output"], "EXPECTED_TOKEN")
            self.assertEqual(timed_out["exit_code"], 124)
            self.assertFalse(timed_out["passed"])

    def test_each_agent_call_uses_an_ephemeral_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = root / "cases.json"
            cases.write_text(
                json.dumps(
                    [{"id": "side-effect", "prompt": "WRITE_SIDE_EFFECT", "expected_checks": ["EXPECTED_TOKEN"]}]
                ),
                encoding="utf-8",
            )
            leaked = ROOT / "eval-side-effect.txt"
            self.assertFalse(leaked.exists())
            result = subprocess.run(
                [
                    sys.executable,
                    str(DRIVER),
                    "--cases",
                    str(cases),
                    "--agent-cmd",
                    f"{sys.executable} {MOCK} {{task}} {{session_root}}",
                    "--session-root",
                    str(root / "sessions"),
                    "--runs",
                    "1",
                    "--threshold",
                    "0",
                    "--artifacts",
                    str(root / "artifacts"),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(leaked.exists())
            artifact = json.loads(
                (root / "artifacts/with_skill/side-effect/run-0.json").read_text(encoding="utf-8")
            )
            self.assertEqual(artifact["cwd_kind"], "ephemeral-temp")


if __name__ == "__main__":
    unittest.main()

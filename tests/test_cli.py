"""CLI diagnostics tests."""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from jarvis.apps.cli import main
from jarvis.application.demo import NOTAS_CONTENT
from jarvis.core.contracts import HealthReport, HealthStatus
from jarvis.core.lifecycle import Supervisor
from jarvis.core.state import RuntimeState


class _StubRuntime:
    """Minimal JarvisRuntime-compatible lifecycle double for CLI tests."""

    def __init__(self, supervisor: Supervisor) -> None:
        self.supervisor = supervisor

    @property
    def state(self) -> RuntimeState:
        return self.supervisor.state

    async def start(self) -> None:
        await self.supervisor.start()

    async def stop(self) -> None:
        await self.supervisor.stop()

    async def run_until_stopped(self, max_turns=None) -> None:
        await self.supervisor.run_until_stopped()

    def diagnostics(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "components": {
                report.name: {
                    "status": report.status.value,
                    "detail": report.detail,
                    "required": report.required,
                }
                for report in self.supervisor.health_snapshot()
            },
            "metrics": {},
            "audio_queue": {},
        }


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.config_path.write_text('{"runtime": {}}', encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_help_lists_run_and_doctor(self) -> None:
        stdout = io.StringIO()

        result = main(["--help"], stdout=stdout, stderr=io.StringIO())

        self.assertEqual(0, result)
        self.assertIn("run", stdout.getvalue())
        self.assertIn("doctor", stdout.getvalue())
        self.assertIn("--use-fakes", stdout.getvalue())

    def test_doctor_real_reports_honest_degraded(self) -> None:
        stdout = io.StringIO()

        result = main(
            ["doctor", "--config", str(self.config_path), "--json"],
            stdout=stdout,
            stderr=io.StringIO(),
        )

        report = json.loads(stdout.getvalue())
        self.assertEqual(1, result)
        self.assertEqual("degraded", report["state"])
        self.assertIn("fast model", report["components"])
        self.assertEqual("degraded", report["components"]["fast model"]["status"])
        self.assertTrue(
            any(
                component["detail"] in {
                    "no model providers configured",
                    "adapter dependency unavailable",
                }
                for component in report["components"].values()
            )
        )

    def _write_ollama_config(self) -> Path:
        path = Path(self.temp_dir.name) / "ollama.json"
        path.write_text(
            json.dumps(
                {
                    "runtime": {},
                    "models": {
                        "providers": [
                            {
                                "kind": "ollama",
                                "base_url": "http://127.0.0.1:1",
                                "model": "mistral",
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_doctor_reports_ollama_unreachable_when_probe_fails(self) -> None:
        config_path = self._write_ollama_config()
        stdout = io.StringIO()

        with patch("jarvis.application.runtime._ollama_reachable", return_value=False):
            result = main(
                ["doctor", "--config", str(config_path), "--json"],
                stdout=stdout,
                stderr=io.StringIO(),
            )

        report = json.loads(stdout.getvalue())
        self.assertEqual(1, result)
        self.assertEqual("degraded", report["state"])
        self.assertEqual("degraded", report["components"]["fast model"]["status"])
        self.assertEqual("Ollama unreachable", report["components"]["fast model"]["detail"])

    def test_doctor_reports_healthy_when_ollama_reachable(self) -> None:
        config_path = self._write_ollama_config()
        stdout = io.StringIO()

        with patch("jarvis.application.runtime._ollama_reachable", return_value=True):
            result = main(
                ["doctor", "--config", str(config_path), "--json"],
                stdout=stdout,
                stderr=io.StringIO(),
            )

        report = json.loads(stdout.getvalue())
        # STT/TTS/network remain degraded in the test environment, so the
        # overall state is still degraded; only the fast model flips to healthy.
        self.assertEqual(1, result)
        self.assertEqual("healthy", report["components"]["fast model"]["status"])
        self.assertEqual("1 provider(s)", report["components"]["fast model"]["detail"])

    def test_ollama_reachable_returns_false_on_connection_and_bad_url(self) -> None:
        from jarvis.application.runtime import _ollama_reachable

        # Closed local port: connection refused raises OSError -> False.
        self.assertFalse(_ollama_reachable("http://127.0.0.1:1", timeout_seconds=0.5))
        # Malformed URL: urlopen raises ValueError/URLError -> False.
        self.assertFalse(_ollama_reachable("not a valid url", timeout_seconds=0.5))

    def test_doctor_stt_tts_detail_names_resolved_provider(self) -> None:
        stdout = io.StringIO()

        result = main(
            ["doctor", "--config", str(self.config_path), "--json"],
            stdout=stdout,
            stderr=io.StringIO(),
        )

        report = json.loads(stdout.getvalue())
        self.assertEqual(1, result)
        self.assertEqual(10, len(report["components"]))
        stt = report["components"]["STT"]
        tts = report["components"]["TTS"]
        self.assertIn(
            stt["detail"],
            {"whisper (faster-whisper)", "adapter dependency unavailable"},
        )
        self.assertIn(
            tts["detail"],
            {"coqui (XTTS-v2)", "adapter dependency unavailable"},
        )

    def test_doctor_use_fakes_json_reports_ready(self) -> None:
        stdout = io.StringIO()

        result = main(
            ["doctor", "--config", str(self.config_path), "--use-fakes", "--json"],
            stdout=stdout,
            stderr=io.StringIO(),
        )

        report = json.loads(stdout.getvalue())
        self.assertEqual(0, result)
        self.assertEqual("ready", report["state"])
        self.assertIn("metrics", report)
        self.assertIn("audio_queue", report)

    def test_accept_command_runs_scripted_file_turn(self) -> None:
        stdout = io.StringIO()

        result = main(
            ["accept", "--config", str(self.config_path), "--json"],
            stdout=stdout,
            stderr=io.StringIO(),
        )

        report = json.loads(stdout.getvalue())
        self.assertEqual(0, result)
        self.assertEqual("ready", report["startup_state"])
        fast_turns = [
            turn for turn in report["turns"] if turn["route"] == "fast_command"
        ]
        self.assertEqual(1, len(fast_turns))
        fast_turn = fast_turns[0]
        self.assertTrue(fast_turn["response"])
        self.assertEqual(NOTAS_CONTENT, fast_turn["response"])
        self.assertGreaterEqual(report["audio_queue"]["played"], 1)

    def test_accept_returns_nonzero_when_fast_command_turn_missing(self) -> None:
        stdout = io.StringIO()

        with patch(
            "jarvis.application.demo.run_hardware_acceptance",
            return_value={
                "startup_state": "ready",
                "shutdown_state": "stopping",
                "turns": [],
                "voice_loop": {"turns": 0},
                "audio_queue": {"played": 0},
            },
        ):
            result = main(
                ["accept", "--config", str(self.config_path), "--json"],
                stdout=stdout,
                stderr=io.StringIO(),
            )

        self.assertEqual(1, result)

    def test_run_check_only_use_fakes_reports_ready(self) -> None:
        stdout = io.StringIO()

        result = main(
            ["run", "--config", str(self.config_path), "--check-only", "--use-fakes"],
            stdout=stdout,
            stderr=io.StringIO(),
        )

        self.assertEqual(0, result)
        self.assertIn("ready", stdout.getvalue())

    def test_invalid_config_returns_actionable_error(self) -> None:
        invalid_path = Path(self.temp_dir.name) / "invalid.json"
        invalid_path.write_text('{"runtime": {"command_deadline_ms": 0}}', encoding="utf-8")
        stderr = io.StringIO()

        result = main(
            ["doctor", "--config", str(invalid_path)],
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(2, result)
        self.assertIn("invalid configuration", stderr.getvalue())
        self.assertIn("command_deadline_ms", stderr.getvalue())

    def test_check_only_returns_nonzero_without_voice_adapters(self) -> None:
        stdout = io.StringIO()

        result = main(
            ["run", "--config", str(self.config_path), "--check-only"],
            stdout=stdout,
            stderr=io.StringIO(),
        )

        self.assertEqual(1, result)
        self.assertIn("degraded", stdout.getvalue())

    def test_shutdown_failure_is_in_final_report_and_exit_status(self):
        class FailsOnStop:
            name = "required cleanup"
            required = True

            async def start(self):
                pass

            async def health(self):
                return HealthReport(self.name, HealthStatus.HEALTHY)

            async def stop(self):
                raise RuntimeError("release failed")

        for args in (["doctor", "--json"], ["run", "--check-only"], ["run"]):
            with self.subTest(args=args):
                runtime = _StubRuntime(Supervisor([FailsOnStop()]))

                async def run_until_stopped(max_turns=None) -> None:
                    await runtime.supervisor.stop()

                stdout = io.StringIO()
                with patch("jarvis.apps.cli.build_runtime", return_value=runtime), patch.object(
                    runtime, "run_until_stopped", side_effect=run_until_stopped
                ):
                    result = main(
                        [*args, "--config", str(self.config_path)],
                        stdout=stdout,
                        stderr=io.StringIO(),
                    )
                self.assertEqual(result, 1)
                self.assertIn("failed", stdout.getvalue())
                self.assertIn("release failed", stdout.getvalue())

    def test_healthy_doctor_still_returns_success_after_normal_shutdown(self):
        runtime = _StubRuntime(Supervisor([]))
        stdout = io.StringIO()
        with patch("jarvis.apps.cli.build_runtime", return_value=runtime):
            result = main(
                ["doctor", "--json", "--config", str(self.config_path)],
                stdout=stdout,
                stderr=io.StringIO(),
            )
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue())["state"], "ready")

    def test_run_enters_voice_loop_when_degraded(self) -> None:
        import asyncio

        from jarvis.apps.cli import _check

        class DegradedRuntime:
            state = RuntimeState.DEGRADED

            def __init__(self) -> None:
                self.loop_runs = 0

            async def start(self) -> None: ...

            async def stop(self) -> None: ...

            async def run_until_stopped(self, max_turns=None) -> None:
                self.loop_runs += 1

            def diagnostics(self) -> dict[str, object]:
                return {"state": RuntimeState.DEGRADED.value}

        runtime = DegradedRuntime()
        report = asyncio.run(_check(runtime, run=True))
        self.assertEqual(1, runtime.loop_runs)
        self.assertEqual(RuntimeState.DEGRADED.value, report["state"])


    def test_run_reports_state_after_keyboard_interrupt(self) -> None:
        import asyncio

        from jarvis.apps.cli import _check

        class InterruptedRuntime:
            state = RuntimeState.DEGRADED

            async def start(self) -> None: ...

            async def stop(self) -> None: ...

            async def run_until_stopped(self, max_turns=None) -> None:
                raise KeyboardInterrupt()

            def diagnostics(self) -> dict[str, object]:
                return {"state": RuntimeState.DEGRADED.value}

        report = asyncio.run(_check(InterruptedRuntime(), run=True))
        self.assertEqual(RuntimeState.DEGRADED.value, report["state"])


if __name__ == "__main__":
    unittest.main()

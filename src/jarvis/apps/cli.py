"""Command-line entry points for the Jarvis runtime.

Commands::

    jarvis doctor    -- health diagnostics
    jarvis run       -- start the runtime and report state
    jarvis demo      -- headless end-to-end acceptance demo (fake adapters)
    jarvis benchmark -- offline latency benchmark (fake adapters)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Sequence, TextIO

from jarvis.application.runtime import JarvisRuntime, build_runtime
from jarvis.config import load_config
from jarvis.core.contracts import HealthStatus
from jarvis.core.state import RuntimeState
from jarvis.observability.logging import configure_logging


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class _Parser(argparse.ArgumentParser):
    def __init__(self, *args: object, stdout: TextIO, stderr: TextIO, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._stdout = stdout
        self._stderr = stderr

    def print_help(self, file: TextIO | None = None) -> None:
        self._stdout.write(self.format_help())

    def error(self, message: str) -> None:
        self._stderr.write(self.format_usage())
        self._stderr.write(f"error: {message}\n")
        raise _ParserExit(2)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            (self._stdout if status == 0 else self._stderr).write(message)
        raise _ParserExit(status)


def _parser(stdout: TextIO, stderr: TextIO) -> _Parser:
    parser = _Parser(
        prog="jarvis",
        description="Jarvis voice assistant runtime.",
        stdout=stdout,
        stderr=stderr,
    )
    parser.add_argument(
        "command", choices=("run", "doctor", "demo", "benchmark"), help="command to execute"
    )
    parser.add_argument("--config", default="config.json", help="path to JSON configuration")
    parser.add_argument(
        "--check-only", action="store_true", help="run health checks without entering the runtime"
    )
    parser.add_argument("--json", action="store_true", help="emit output as JSON")
    parser.add_argument(
        "--use-fakes",
        action="store_true",
        help="compose the offline fake-adapter runtime",
    )
    return parser


async def _check(runtime: JarvisRuntime, *, run: bool = False) -> dict[str, object]:
    try:
        await runtime.start()
        checked_state = runtime.state
        if run and checked_state is RuntimeState.READY:
            loop = asyncio.get_running_loop()

            def stop_runtime() -> None:
                loop.create_task(runtime.stop())

            for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(shutdown_signal, stop_runtime)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass
            await runtime.run_until_stopped()
    finally:
        await runtime.stop()
    report = runtime.diagnostics()
    if runtime.state is RuntimeState.STOPPING:
        if checked_state is RuntimeState.READY and any(
            health.status is not HealthStatus.HEALTHY
            for health in runtime.supervisor.health_snapshot()
        ):
            checked_state = RuntimeState.DEGRADED
        report["state"] = checked_state.value
    return report


def _write_report(report: dict[str, object], stdout: TextIO, as_json: bool) -> None:
    if as_json:
        json.dump(report, stdout, sort_keys=True)
        stdout.write("\n")
        return
    stdout.write(f"state: {report['state']}\n")
    components = report["components"]
    assert isinstance(components, dict)
    for name, health in components.items():
        assert isinstance(health, dict)
        detail = health["detail"]
        stdout.write(f"{name}: {health['status']}" + (f" ({detail})" if detail else "") + "\n")

    metrics = report.get("metrics", {})
    if isinstance(metrics, dict):
        if metrics:
            summary = ", ".join(
                f"{name}: count={values.get('count')}, p50={values.get('p50')}, p95={values.get('p95')}"
                for name, values in metrics.items()
                if isinstance(values, dict)
            )
            stdout.write(f"metrics: {summary or 'none'}\n")
        else:
            stdout.write("metrics: none\n")

    audio_queue = report.get("audio_queue", {})
    if isinstance(audio_queue, dict):
        stdout.write(
            "audio_queue: "
            + ", ".join(f"{name}={value}" for name, value in audio_queue.items())
            + "\n"
        )


def _temp_config(config):
    """Point memory at a temporary database so demo/benchmark do not pollute the repo."""
    return replace(
        config, memory=replace(config.memory, db_path=str(Path(tempfile.mkdtemp()) / "jarvis.db"))
    )


def _run_demo_command(config, stdout: TextIO, as_json: bool) -> int:
    from jarvis.application.demo import run_demo

    config = _temp_config(config)
    result = run_demo(config)
    if as_json:
        json.dump(result, stdout, sort_keys=True, ensure_ascii=False)
        stdout.write("\n")
    else:
        _print_demo(result, stdout)
    return 0 if result["startup_state"] == "ready" else 1


def _print_demo(result: dict, stdout: TextIO) -> None:
    stdout.write(f"startup_state: {result['startup_state']}\n")
    stdout.write(f"shutdown_state: {result['shutdown_state']}\n")
    for turn in result["turns"]:
        stdout.write(f"turn {turn['route']}: {turn['text']!r} -> {turn['response']!r}\n")
    stdout.write(f"memory_recall: {result['memory_recall']}\n")


def _run_benchmark_command(config, stdout: TextIO, as_json: bool) -> int:
    from jarvis.benchmarks import run_benchmarks

    config = _temp_config(config)
    result = run_benchmarks(config)
    if as_json:
        json.dump(result, stdout, sort_keys=True, ensure_ascii=False)
        stdout.write("\n")
    else:
        stdout.write(f"cold_startup_ms: {result['cold_startup_ms']}\n")
        stdout.write(f"barge_in_ms: {result['barge_in_ms']}\n")
        for turn in result["turns"]:
            stdout.write(
                f"turn {turn['route']}: {turn['text']!r} -> {turn['elapsed_ms']} ms\n"
            )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI with injectable streams for deterministic diagnostics tests."""
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    parser = _parser(output, errors)
    try:
        args = parser.parse_args(argv)
    except _ParserExit as error:
        return error.status

    if args.command == "doctor" and args.check_only:
        errors.write("error: --check-only is only valid with run\n")
        return 2
    if args.command == "run" and args.json:
        errors.write("error: --json is only valid with doctor\n")
        return 2

    try:
        config = load_config(Path(args.config))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.write(f"error: invalid configuration: {error}\n")
        return 2

    if args.command == "demo":
        return _run_demo_command(config, output, args.json)
    if args.command == "benchmark":
        return _run_benchmark_command(config, output, args.json)

    configure_logging(logging.getLogger("jarvis.cli"), stream=errors)
    runtime = build_runtime(config, use_fakes=args.use_fakes)
    try:
        report = asyncio.run(_check(runtime, run=args.command == "run" and not args.check_only))
    except Exception as error:
        errors.write(f"error: runtime failure: {error}\n")
        return 1
    _write_report(report, output, args.json)
    return 0 if report["state"] == RuntimeState.READY.value else 1

"""Command-line diagnostics for the Jarvis foundation runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Sequence, TextIO

from jarvis.config import load_config
from jarvis.core.state import RuntimeState
from jarvis.observability.logging import configure_logging

from .runtime import FoundationRuntime, create_foundation_runtime


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
        description="Jarvis foundation runtime diagnostics.",
        stdout=stdout,
        stderr=stderr,
    )
    parser.add_argument("command", choices=("run", "doctor"), help="command to execute")
    parser.add_argument("--config", default="config.json", help="path to JSON configuration")
    parser.add_argument(
        "--check-only", action="store_true", help="run health checks without entering the runtime"
    )
    parser.add_argument("--json", action="store_true", help="emit diagnostics as JSON")
    return parser


async def _check(runtime: FoundationRuntime) -> dict[str, object]:
    try:
        return await runtime.check()
    finally:
        await runtime.stop()


async def _run(runtime: FoundationRuntime) -> dict[str, object]:
    try:
        report = await runtime.check()
        if runtime.state is RuntimeState.READY:
            await runtime.supervisor.run_until_stopped()
            report = runtime.diagnostics()
        return report
    finally:
        await runtime.stop()


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

    configure_logging(logging.getLogger("jarvis.cli"), stream=errors)
    runtime = create_foundation_runtime(config)
    try:
        report = asyncio.run(_check(runtime) if args.command == "doctor" or args.check_only else _run(runtime))
    except Exception as error:
        errors.write(f"error: runtime failure: {error}\n")
        return 1
    _write_report(report, output, args.json)
    return 0 if report["state"] == RuntimeState.READY.value else 1

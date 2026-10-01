"""scripts/perf_bench.py: PID-scoped resource sampling and chime-callback bookkeeping (no audio, no psutil)."""

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_cwd = os.getcwd()
_spec = importlib.util.spec_from_file_location("perf_bench", ROOT / "scripts" / "perf_bench.py")
perf_bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(perf_bench)
os.chdir(_cwd)  # the script chdirs to the repo root on import


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class ChimeProbeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.probe = perf_bench.ChimeProbe(self.clock)

    def test_idle_callbacks_before_arm_never_count_as_the_chime(self):
        self.probe.observe(0.0)
        self.probe.observe(0.9)
        self.assertEqual(self.probe.idle_callbacks, 2)
        self.assertIsNone(self.probe.latency_ms())

    def test_first_non_silent_callback_after_arm_is_the_latency(self):
        self.probe.arm()
        self.clock.now += 0.010
        self.probe.observe(0.0)  # silent prime/idle block after the trigger
        self.assertIsNone(self.probe.latency_ms())
        self.clock.now += 0.020
        self.probe.observe(0.5)
        self.clock.now += 1.0
        self.probe.observe(0.7)  # later blocks must not overwrite
        self.assertAlmostEqual(self.probe.latency_ms(), 30.0, places=3)

    def test_never_audible_is_missing_not_zero(self):
        self.probe.arm()
        self.probe.observe(0.0)
        self.assertIsNone(self.probe.latency_ms())

    def test_rearm_resets_previous_result(self):
        self.probe.arm()
        self.clock.now += 0.1
        self.probe.observe(0.5)
        self.probe.arm()
        self.assertIsNone(self.probe.latency_ms())


class StartupBookkeepingTests(unittest.TestCase):
    def test_trial_row_keeps_unobserved_marks_as_none(self):
        clock = Clock()
        probe = perf_bench.ChimeProbe(clock)
        row = perf_bench.startup_trial_row(100.0, probe, None, {"first_sound": 3.2})
        self.assertIsNone(row["gesture_to_chime_callback_ms"])
        self.assertIsNone(row["welcome_audio_ms"])
        self.assertIsNone(row["music_started_ms"])
        self.assertEqual(row["report_first_sound_ms"], 3.2)

    def test_trial_row_reports_welcome_relative_to_trigger(self):
        row = perf_bench.startup_trial_row(100.0, perf_bench.ChimeProbe(Clock()), 102.5, {})
        self.assertAlmostEqual(row["welcome_audio_ms"], 2500.0)

    def test_summary_counts_missing_and_excludes_them_from_stats(self):
        rows = [
            {"gesture_to_chime_callback_ms": 40.0, "report_first_sound_ms": 2.0, "music_started_ms": None, "welcome_audio_ms": None},
            {"gesture_to_chime_callback_ms": None, "report_first_sound_ms": 4.0, "music_started_ms": None, "welcome_audio_ms": None},
        ]
        summary = perf_bench.summarize_startup(rows)
        self.assertEqual(summary["gesture_to_chime_callback_ms"]["n"], 1)
        self.assertEqual(summary["gesture_to_chime_callback_ms"]["missing"], 1)
        self.assertEqual(summary["music_started_ms"]["n"], 0)
        self.assertEqual(summary["music_started_ms"]["missing"], 2)
        self.assertEqual(summary["report_first_sound_ms"]["p50"], 3.0)


class PidSelectionTests(unittest.TestCase):
    DAEMON = ["pythonw.exe", "C:\\jarvis\\scripts\\jarvis_daemon.pyw", "--config", "config.win.json"]

    def test_shim_and_child_with_same_cmdline_count_once(self):
        rows = [(10, 1, self.DAEMON), (11, 10, self.DAEMON), (12, 1, ["explorer.exe"]), (13, 1, None)]
        self.assertEqual(perf_bench.daemon_roots(rows), [10])
        self.assertEqual(perf_bench.resolve_daemon_pid(None, rows), (10, None))

    def test_no_daemon_is_an_error(self):
        pid, error = perf_bench.resolve_daemon_pid(None, [(12, 1, ["explorer.exe"])])
        self.assertIsNone(pid)
        self.assertIn("no jarvis_daemon.pyw", error)

    def test_two_instances_are_never_aggregated(self):
        rows = [(10, 1, self.DAEMON), (20, 1, self.DAEMON)]
        pid, error = perf_bench.resolve_daemon_pid(None, rows)
        self.assertIsNone(pid)
        self.assertIn("--pid", error)
        self.assertIn("10", error)
        self.assertIn("20", error)

    def test_explicit_pid_ignores_other_instances(self):
        self.assertEqual(perf_bench.resolve_daemon_pid(20, [(10, 1, self.DAEMON), (20, 1, self.DAEMON)]), (20, None))


class FakePsutil:
    """Just enough psutil: a process table with scripted per-sample CPU/RSS."""

    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    def __init__(self):
        self.table = {}
        self.Process = self._process

    def add(self, pid, name, cpu, rss_mb=100, children=(), dies_after=None):
        proc = types.SimpleNamespace(pid=pid, cpu=list(cpu), rss=rss_mb * 2**20, calls=0, dies_after=dies_after, kids=list(children))
        proc.name = lambda: name
        proc.cpu_percent = lambda interval=None, p=proc: self._cpu(p)
        proc.memory_info = lambda p=proc: types.SimpleNamespace(rss=p.rss)
        proc.children = lambda recursive=True, p=proc: [self.table[k] for k in p.kids]
        self.table[pid] = proc
        return proc

    def _cpu(self, proc):
        proc.calls += 1
        if proc.dies_after is not None and proc.calls > proc.dies_after + 1:  # call 1 is the priming call
            raise self.NoSuchProcess()
        index = proc.calls - 2
        return proc.cpu[index] if 0 <= index < len(proc.cpu) else 0.0

    def _process(self, pid):
        if pid not in self.table:
            raise self.NoSuchProcess()
        return self.table[pid]


class SampleResourcesTests(unittest.TestCase):
    def sample(self, psutil_mod, pid=10, gpu=lambda: 1200.0, window_s=3.0):
        sleeps = []
        result = perf_bench.sample_resources(pid, psutil_mod, gpu, window_s=window_s, interval_s=1.0, sleep=sleeps.append)
        return result, sleeps

    def test_sums_only_target_and_its_children(self):
        fake = FakePsutil()
        fake.add(10, "pythonw.exe", [1.0, 2.0, 3.0], rss_mb=100, children=[11])
        fake.add(11, "python.exe", [0.5, 0.5, 0.5], rss_mb=50)
        fake.add(99, "pythonw.exe", [80.0, 80.0, 80.0], rss_mb=999)  # unrelated Jarvis instance
        result, sleeps = self.sample(fake)
        self.assertEqual(sleeps, [1.0, 1.0, 1.0])
        self.assertEqual(result["samples"], 3)
        self.assertEqual(result["process_count"], 2)
        self.assertEqual(result["cpu_percent_one_core"], {"mean": 2.5, "max": 3.5})
        self.assertEqual(result["rss_mb"], {"last": 150.0, "peak": 150.0})
        self.assertNotIn("error", result)
        self.assertEqual(result["gpu"], {"available": True, "used_mb_before": 1200.0, "used_mb_after": 1200.0})

    def test_absent_pid_is_an_error_without_sleeping(self):
        result, sleeps = self.sample(FakePsutil(), pid=4242)
        self.assertIn("error", result)
        self.assertIn("4242", result["error"])
        self.assertNotIn("cpu_percent_one_core", result)
        self.assertEqual(sleeps, [])

    def test_missing_gpu_is_reported_unavailable_not_zero(self):
        fake = FakePsutil()
        fake.add(10, "pythonw.exe", [1.0, 1.0, 1.0])
        result, _ = self.sample(fake, gpu=lambda: None)
        self.assertEqual(result["gpu"]["available"], False)
        self.assertNotIn("used_mb_before", result["gpu"])

    def test_gpu_baseline_records_change_across_window(self):
        fake = FakePsutil()
        fake.add(10, "pythonw.exe", [1.0, 1.0, 1.0])
        readings = iter([500.0, 900.0])
        result, _ = self.sample(fake, gpu=lambda: next(readings))
        self.assertEqual((result["gpu"]["used_mb_before"], result["gpu"]["used_mb_after"]), (500.0, 900.0))

    def test_daemon_exit_mid_window_flags_partial_result(self):
        fake = FakePsutil()
        fake.add(10, "pythonw.exe", [1.0, 1.0, 1.0], dies_after=1)
        result, sleeps = self.sample(fake)
        self.assertIn("exited during the window", result["error"])
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(result["samples"], 2)

    def test_child_exit_is_counted_but_not_fatal(self):
        fake = FakePsutil()
        fake.add(10, "pythonw.exe", [1.0, 1.0, 1.0], children=[11])
        fake.add(11, "python.exe", [2.0, 2.0, 2.0], dies_after=1)
        result, _ = self.sample(fake)
        self.assertNotIn("error", result)
        self.assertEqual(result["processes_exited_during_window"], 1)
        self.assertEqual(result["samples"], 3)


class BenchResourcesTests(unittest.TestCase):
    def setUp(self):
        perf_bench.RESULTS.pop("resources", None)
        self.addCleanup(perf_bench.RESULTS.pop, "resources", None)

    def test_missing_psutil_is_an_error(self):
        with mock.patch.dict(sys.modules, {"psutil": None}):
            perf_bench.bench_resources(None)
        self.assertIn("psutil unavailable", perf_bench.RESULTS["resources"]["error"])

    def test_no_daemon_running_is_an_error_not_a_zero_sample(self):
        fake = types.SimpleNamespace(process_iter=lambda attrs: [])
        with mock.patch.dict(sys.modules, {"psutil": fake}):
            perf_bench.bench_resources(None)
        self.assertIn("no jarvis_daemon.pyw", perf_bench.RESULTS["resources"]["error"])

    def test_explicit_pid_skips_process_discovery(self):
        fake = FakePsutil()
        fake.process_iter = lambda attrs: self.fail("must not sweep the process table with --pid")
        with mock.patch.dict(sys.modules, {"psutil": fake}):
            perf_bench.bench_resources(None, pid=555)
        self.assertIn("555", perf_bench.RESULTS["resources"]["error"])


class StatsTests(unittest.TestCase):
    def test_empty_is_n_zero(self):
        self.assertEqual(perf_bench.stats([]), {"n": 0})

    def test_none_values_are_filtered(self):
        self.assertEqual(perf_bench.stats([None, None]), {"n": 0})
        self.assertEqual(perf_bench.stats([None, 4.0])["n"], 1)

    def test_single_value_has_equal_p50_and_p95(self):
        block = perf_bench.stats([7.0])
        self.assertEqual((block["p50"], block["p95"]), (7.0, 7.0))

    def test_p95_index_on_twenty_values(self):
        block = perf_bench.stats([float(i) for i in range(1, 21)])  # round(0.95 * 19) = 18 -> value 19
        self.assertEqual(block["p95"], 19.0)
        self.assertEqual(block["p50"], 10.5)
        self.assertEqual((block["min"], block["max"]), (1.0, 20.0))


def block(p50, p95, n=5):
    return {"n": n, "p50": p50, "p95": p95}


class CollectStatsTests(unittest.TestCase):
    def test_nested_paths_and_skipped_keys(self):
        results = {
            "meta": {"x": block(1, 2)},
            "_stt_instance": block(1, 2),
            "claps": {"method": "text", "confirm_ms": block(555.4, 700.0), "detected": "40/40"},
            "startup": {"cold": {"chime_ms": block(10, 20) | {"values": [10], "missing": 0}}},
            "tts": {"warm_ttfa_ms": {"n": 0}, "vram": 100},
            "resources": {"error": "psutil unavailable"},
        }
        self.assertEqual(
            sorted(perf_bench.collect_stats(results)),
            ["claps.confirm_ms", "startup.cold.chime_ms", "tts.warm_ttfa_ms"],
        )


class CompareTests(unittest.TestCase):
    def rows(self, baseline, current):
        return {row[0]: row[1:] for row in perf_bench.compare(baseline, current)}

    def test_normal_delta_sign(self):
        rows = self.rows({"a": {"m": block(100.0, 120.0)}}, {"a": {"m": block(150.0, 180.0), "faster": block(50.0, 60.0)}})
        self.assertEqual(rows["a.m"], ("100.0 / 120.0", "150.0 / 180.0", "+50.0%"))
        faster = self.rows({"a": {"m": block(200.0, 1.0)}}, {"a": {"m": block(100.0, 1.0)}})
        self.assertEqual(faster["a.m"][2], "-50.0%")

    def test_metric_on_one_side_only(self):
        rows = self.rows({"a": {"old": block(1.0, 2.0)}}, {"a": {"new": block(3.0, 4.0)}})
        self.assertEqual(rows["a.old"], ("1.0 / 2.0", "—", "—"))
        self.assertEqual(rows["a.new"], ("—", "3.0 / 4.0", "—"))

    def test_n_zero_side_shows_missing(self):
        rows = self.rows({"a": {"m": {"n": 0}}}, {"a": {"m": block(3.0, 4.0)}})
        self.assertEqual(rows["a.m"], ("—", "3.0 / 4.0", "—"))

    def test_zero_baseline_p50_has_no_delta(self):
        rows = self.rows({"a": {"m": block(0.0, 1.0)}}, {"a": {"m": block(3.0, 4.0)}})
        self.assertEqual(rows["a.m"], ("0.0 / 1.0", "3.0 / 4.0", "—"))

    def test_rows_are_sorted_by_metric(self):
        names = [row[0] for row in perf_bench.compare({"b": {"x": block(1, 1)}}, {"a": {"x": block(1, 1)}})]
        self.assertEqual(names, ["a.x", "b.x"])

    def test_error_sections_are_ignored(self):
        self.assertEqual(perf_bench.compare({"resources": {"error": "x"}}, {"resources": {"error": "y"}}), [])


class FormatComparisonTests(unittest.TestCase):
    def test_header_and_row(self):
        text = perf_bench.format_comparison([("a.m", "1 / 2", "3 / 4", "+200.0%")])
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("| metric |"))
        self.assertEqual(lines[1], "|---|---|---|---|")
        self.assertEqual(lines[2], "| a.m | 1 / 2 | 3 / 4 | +200.0% |")


class CompareOnlyCliTests(unittest.TestCase):
    def test_compare_only_skips_sections_and_config(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base, cur = Path(tmp) / "b.json", Path(tmp) / "c.json"
            base.write_text(json.dumps({"a": {"m": block(100.0, 120.0)}}), encoding="utf-8")
            cur.write_text(json.dumps({"a": {"m": block(110.0, 130.0)}}), encoding="utf-8")
            argv = ["perf_bench.py", "--compare-only", str(cur), "--compare", str(base)]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(perf_bench, "load_config", side_effect=AssertionError("config must not load")), \
                    mock.patch.dict(perf_bench.SECTIONS, {k: mock.Mock(side_effect=AssertionError("no sections")) for k in perf_bench.SECTIONS}), \
                    mock.patch("builtins.print") as out:
                self.assertEqual(perf_bench.main(), 0)
            self.assertIn("a.m", out.call_args[0][0])
            self.assertIn("+10.0%", out.call_args[0][0])

    def test_compare_only_without_baseline_is_an_error(self):
        with mock.patch.object(sys, "argv", ["perf_bench.py", "--compare-only", "x.json"]), self.assertRaises(SystemExit):
            perf_bench.main()


if __name__ == "__main__":
    unittest.main()

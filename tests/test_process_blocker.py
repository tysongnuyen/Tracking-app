import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from process_blocker import ProcessBlocker, canonical_path


class FakeProcess:
    def __init__(self, world, pid, exe, ppid=0, created=None, name=None):
        self.world, self.pid = world, pid
        self.path, self.parent = str(exe), ppid
        self.created = float(created if created is not None else pid)
        self.label = name if name is not None else Path(exe).name
        self.denied = set()
        self.kill_denied = False
        self.ignore_kill = False
        self.after_kill = None
        self.after_suspend = None
        self.suspended = False

    def suspend(self):
        self.suspended = True
        if self.after_suspend:
            callback, self.after_suspend = self.after_suspend, None
            callback()

    def resume(self):
        self.suspended = False

    def _value(self, key, value):
        if key in self.denied:
            raise psutil.AccessDenied(self.pid)
        return value

    def create_time(self): return self._value("create_time", self.created)
    def exe(self): return self._value("exe", self.path)
    def ppid(self): return self._value("ppid", self.parent)
    def name(self): return self._value("name", self.label)

    def kill(self):
        if self.kill_denied:
            raise psutil.AccessDenied(self.pid)
        self.world.kills.append(self.pid)
        if not self.ignore_kill:
            self.world.processes.pop(self.pid, None)
        if self.after_kill:
            self.after_kill()


class World:
    def __init__(self):
        self.processes = {}
        self.kills = []
        self.before_get = None
        self.iter_error = None

    def add(self, pid, exe, **kwargs):
        process = FakeProcess(self, pid, exe, **kwargs)
        self.processes[pid] = process
        return process

    def iterate(self):
        if self.iter_error:
            raise self.iter_error
        return list(self.processes.values())

    def get(self, pid):
        if self.before_get:
            callback, self.before_get = self.before_get, None
            callback(pid)
        if pid not in self.processes:
            raise psutil.NoSuchProcess(pid)
        return self.processes[pid]


class ProcessBlockerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tracking-blocker-")
        self.folder = Path(self.temp.name)
        self.app = self.exe("chosen.exe")
        self.helper = self.exe("helper.exe")
        self.other = self.exe("other.exe")
        self.world = World()
        self.blocker = ProcessBlocker(process_iter=self.world.iterate, process_factory=self.world.get)

    def tearDown(self):
        self.temp.cleanup()

    def exe(self, name):
        file = self.folder / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"test fixture, never executed")
        return str(file)

    def scan(self):
        return self.blocker.scan([self.app])[canonical_path(self.app)]

    def test_no_selected_rule_does_nothing(self):
        self.world.add(100, self.app)
        self.assertEqual(self.blocker.scan([]), {})
        self.assertEqual(self.world.kills, [])
        self.assertFalse(self.blocker.contains(self.app, 100))

    def test_path_requires_existing_exe_and_rejects_tracker_and_critical_windows(self):
        self.assertEqual(self.blocker.validate_path(self.app), canonical_path(self.app))
        for invalid in ("", self.folder, self.folder / "missing.exe", self.exe("app.txt"),
                        self.exe("svchost.exe"), sys.executable):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.blocker.validate_path(invalid)

    def test_all_instances_and_different_executable_descendants_are_killed(self):
        self.world.add(100, self.app, created=10)
        self.world.add(101, self.helper, ppid=100, created=11)
        self.world.add(102, self.other, ppid=101, created=12)
        self.world.add(200, self.app, created=20)
        self.world.add(201, self.helper, ppid=200, created=21)
        self.world.add(300, self.other, created=30)
        snapshot = self.scan()
        self.assertEqual(snapshot["pids"], {100, 101, 102, 200, 201})
        self.assertTrue(self.blocker.contains(self.app, 102))
        result = self.blocker.terminate(self.app)
        self.assertEqual(set(result["killed"]), snapshot["pids"])
        self.assertEqual(result["alive"], [])
        self.assertEqual(result["denied"], [])
        self.assertEqual(set(self.world.processes), {300})
        self.assertLess(self.world.kills.index(102), self.world.kills.index(101))
        self.assertLess(self.world.kills.index(101), self.world.kills.index(100))

    def test_same_basename_at_other_path_is_not_selected(self):
        duplicate = self.exe("another/chosen.exe")
        self.world.add(100, self.app)
        self.world.add(200, duplicate)
        self.blocker.terminate(self.app)
        self.assertEqual(self.world.kills, [100])
        self.assertIn(200, self.world.processes)

    def test_children_are_remembered_after_root_exits_and_keep_their_descendants(self):
        self.world.add(100, self.app)
        self.world.add(101, self.helper, ppid=100)
        self.scan()
        del self.world.processes[100]
        self.world.add(102, self.other, ppid=101)
        self.assertEqual(self.scan()["pids"], {101, 102})
        result = self.blocker.terminate(self.app)
        self.assertEqual(set(result["killed"]), {101, 102})

    def test_reopened_app_is_discovered_after_previous_process_exits(self):
        self.world.add(100, self.app)
        self.blocker.terminate(self.app)
        self.world.add(200, self.app)
        self.assertEqual(self.blocker.terminate(self.app)["killed"], [200])

    def test_child_spawned_just_before_suspension_is_seen_on_next_pass(self):
        self.world.add(100, self.app)
        child = self.world.add(101, self.helper, ppid=100)
        child.after_suspend = lambda: self.world.add(102, self.helper, ppid=101)
        # PID 102 is created after the initial family snapshot but before the
        # child is held. The next scan must capture it before parent shutdown.
        result = self.blocker.terminate(self.app)
        self.assertEqual(result["alive"], [])
        self.assertIn(102, result["killed"])

    def test_pid_reuse_after_snapshot_never_kills_the_new_process(self):
        self.world.add(100, self.app)
        self.world.add(101, self.helper, ppid=100)
        self.scan()
        del self.world.processes[100]
        self.world.add(101, self.other, ppid=0, created=1000)
        result = self.blocker.terminate(self.app)
        self.assertEqual(result["killed"], [])
        self.assertIn(101, self.world.processes)

    def test_pid_reuse_between_scan_and_kill_is_reverified(self):
        self.world.add(100, self.app)
        def replace(pid):
            self.assertEqual(pid, 100)
            self.world.add(100, self.other, created=500)
        self.world.before_get = replace
        result = self.blocker.terminate(self.app)
        self.assertEqual(result["killed"], [])
        self.assertIn(100, self.world.processes)

    def test_executable_mismatch_before_kill_is_not_killed(self):
        self.world.add(100, self.app)
        self.world.before_get = lambda pid: self.world.add(pid, self.other, created=100)
        self.blocker.terminate(self.app)
        self.assertEqual(self.world.kills, [])

    def test_stale_ppid_from_reused_parent_is_not_a_child(self):
        self.world.add(100, self.app, created=500)
        self.world.add(101, self.other, ppid=100, created=100)
        self.assertEqual(self.scan()["pids"], {100})
        self.blocker.terminate(self.app)
        self.assertIn(101, self.world.processes)

    def test_kill_permission_failure_is_explicit_and_surviving(self):
        self.world.add(100, self.app).kill_denied = True
        result = self.blocker.terminate(self.app)
        self.assertEqual(result["alive"], [100])
        self.assertEqual(result["denied"], [100])
        self.assertEqual(result["killed"], [])

    def test_successful_kill_request_is_not_a_confirmed_exit(self):
        process = self.world.add(100, self.app)
        process.ignore_kill = True
        result = self.blocker.terminate(self.app, passes=50)
        self.assertEqual(self.world.kills, [100])
        self.assertEqual(result["alive"], [100])
        self.assertFalse(process.suspended)

    def test_known_child_metadata_access_denied_is_retained_and_reported(self):
        self.world.add(100, self.app)
        child = self.world.add(101, self.helper, ppid=100)
        self.scan()
        del self.world.processes[100]
        child.denied.update({"exe", "create_time"})
        result = self.blocker.terminate(self.app)
        self.assertEqual(result["denied"], [101])
        self.assertEqual(result["alive"], [101])
        self.assertEqual(self.world.kills, [])
        child.denied.clear()
        self.assertEqual(self.blocker.terminate(self.app)["killed"], [101])

    def test_unknown_matching_name_denial_is_reported_without_name_based_kill(self):
        self.world.add(100, self.app).denied.add("exe")
        self.world.add(200, self.other).denied.add("exe")
        snapshot = self.scan()
        self.assertEqual(snapshot["denied"], {100})
        self.assertFalse(self.blocker.contains(self.app, 100))
        result = self.blocker.terminate(self.app)
        self.assertEqual(result["alive"], [100])
        self.assertEqual(self.world.kills, [])

    def test_critical_child_is_explicitly_protected(self):
        self.world.add(100, self.app)
        self.world.add(101, self.helper, ppid=100, name="lsass.exe")
        result = self.blocker.terminate(self.app)
        self.assertEqual(result["protected"], [101])
        self.assertEqual(result["alive"], [101])
        self.assertEqual(self.world.kills, [100])

    def test_external_excluded_pid_set_is_live_and_protects_descendants(self):
        excluded = set()
        self.blocker.excluded_pids = excluded
        self.world.add(100, self.app)
        self.world.add(101, self.helper, ppid=100)
        excluded.add(100)
        result = self.blocker.terminate(self.app)
        self.assertEqual(result["protected"], [100])
        self.assertEqual(self.world.kills, [])
        self.assertIn(101, self.world.processes)

    def test_tracker_ancestor_is_protected_without_protecting_other_app_instance(self):
        self.world.add(100, self.app, created=10)
        self.world.add(os.getpid(), self.other, ppid=100, created=20)
        self.world.add(200, self.app, created=30)
        result = self.blocker.terminate(self.app)
        self.assertEqual(result["protected"], [100])
        self.assertEqual(self.world.kills, [200])

    def test_scanning_one_rule_does_not_infect_another_rules_family(self):
        self.world.add(100, self.app)
        self.world.add(101, self.helper, ppid=100)
        self.world.add(200, self.other)
        self.blocker.scan([self.app, self.other])
        del self.world.processes[100]
        self.blocker.terminate(self.other)
        self.assertTrue(self.blocker.contains(self.app, 101))

    def test_removing_rule_forgets_cached_orphans(self):
        self.world.add(100, self.app)
        self.world.add(101, self.helper, ppid=100)
        self.scan()
        del self.world.processes[100]
        self.blocker.scan([])
        self.assertEqual(self.scan()["pids"], set())
        self.assertEqual(self.world.kills, [])

    def test_global_discovery_failure_cannot_report_silent_success(self):
        self.world.iter_error = psutil.AccessDenied()
        result = self.blocker.terminate(self.app)
        self.assertTrue(result["errors"])
        self.assertEqual(self.world.kills, [])

    def test_incomplete_discovery_does_not_forget_known_orphans(self):
        self.world.add(100, self.app)
        self.world.add(101, self.helper, ppid=100)
        self.scan()
        del self.world.processes[100]
        self.world.iter_error = psutil.AccessDenied()
        result = self.blocker.terminate(self.app)
        self.assertEqual(set(result["denied"]), {100, 101})
        self.world.iter_error = None
        self.assertEqual(self.blocker.terminate(self.app)["killed"], [101])

    def test_known_orphan_creation_time_denial_does_not_lose_its_identity(self):
        self.world.add(100, self.app)
        child = self.world.add(101, self.helper, ppid=100)
        self.scan()
        del self.world.processes[100]
        child.denied.add("create_time")
        self.assertEqual(self.blocker.terminate(self.app)["denied"], [101])
        child.denied.clear()
        self.assertEqual(self.blocker.terminate(self.app)["killed"], [101])

    @unittest.skipUnless(os.name == "nt", "Windows copied-runtime integration fixture")
    def test_real_copied_runtime_tree_and_orphan_are_stopped_by_sibling_checker(self):
        """Only unique executables copied into this disposable test directory."""
        fixture = self.folder / "runtime"
        fixture.mkdir()
        base = Path(sys.base_prefix)
        for dll in base.glob("*.dll"):
            shutil.copy2(dll, fixture / dll.name)
        executables = [fixture / f"tracking-test-{role}.exe" for role in ("root", "child", "grand")]
        for exe in executables:
            shutil.copy2(sys._base_executable, exe)
        script = fixture / "fixture.py"
        script.write_text('''import json,os,pathlib,subprocess,sys,time
folder=pathlib.Path(__file__).parent
role=sys.argv[1]
if role=="root": subprocess.Popen([str(folder/"tracking-test-child.exe"),__file__,"child"],creationflags=0x08000000)
if role=="child": subprocess.Popen([str(folder/"tracking-test-grand.exe"),__file__,"grand"],creationflags=0x08000000)
(folder/(role+".json")).write_text(json.dumps({"pid":os.getpid()}))
while not (role=="root" and (folder/"exit-root").exists()): time.sleep(.02)
''', encoding="utf-8")
        checker = fixture / "checker.py"
        checker.write_text('''import faulthandler,json,pathlib,sys,time
sys.path.insert(0,sys.argv[1])
from process_blocker import ProcessBlocker,canonical_path
import psutil
folder=pathlib.Path(__file__).parent
trace=(folder/"trace.txt").open("w")
faulthandler.dump_traceback_later(5,repeat=True,file=trace)
app=str(folder/"tracking-test-root.exe")
blocker=ProcessBlocker()
snapshot=blocker.scan([app])[canonical_path(app)]
(folder/"scanned.json").write_text(json.dumps({"pids":sorted(snapshot["pids"]),"protected":sorted(snapshot["protected"]),"details":[[p,psutil.Process(p).name(),psutil.Process(p).exe(),psutil.Process(p).ppid()] for p in snapshot["pids"]]}))
deadline=time.monotonic()+15
while not (folder/"go").exists() and time.monotonic()<deadline: time.sleep(.02)
results=[]
for _ in range(3):
    result=blocker.terminate(app);results.append(result)
    if not result["alive"]: break
    time.sleep(.1)
(folder/"result.json").write_text(json.dumps(results))
''', encoding="utf-8")
        environment = os.environ.copy()
        environment["PYTHONHOME"] = str(base)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        source = str(Path(__file__).resolve().parents[1] / "src")
        # Each fixture process is a sibling of the checker, not its descendant.
        # The production protection of Tracking App's children remains enabled.
        for orphan in (False, True):
            with self.subTest(orphan=orphan):
                for name in ("root.json", "child.json", "grand.json", "scanned.json", "result.json", "go", "exit-root"):
                    (fixture / name).unlink(missing_ok=True)
                processes, expected = [], {}
                try:
                    root = subprocess.Popen([str(executables[0]), str(script), "root"], env=environment,
                                            creationflags=subprocess.CREATE_NO_WINDOW,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    processes.append(root)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and not all((fixture / f"{role}.json").exists() for role in ("root", "child", "grand")):
                        if root.poll() is not None:
                            self.fail(root.communicate()[1].decode(errors="replace"))
                        time.sleep(.02)
                    for role in ("root", "child", "grand"):
                        pid = json.loads((fixture / f"{role}.json").read_text())["pid"]
                        process = psutil.Process(pid)
                        expected[pid] = (process.create_time(), canonical_path(process.exe()))
                    worker = subprocess.Popen([sys.executable, str(checker), source], env=os.environ.copy(),
                                              creationflags=subprocess.CREATE_NO_WINDOW,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    processes.append(worker)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and not (fixture / "scanned.json").exists():
                        if worker.poll() is not None:
                            self.fail(worker.communicate()[1].decode(errors="replace"))
                        time.sleep(.02)
                    snapshot = json.loads((fixture / "scanned.json").read_text())
                    self.assertTrue(set(expected).issubset(snapshot["pids"]), snapshot)
                    script_pids = set(expected)
                    # CREATE_NO_WINDOW still has dedicated conhost descendants
                    # on some Windows versions. They belong to this fixture.
                    for pid in set(snapshot["pids"]) - script_pids:
                        process = psutil.Process(pid)
                        self.assertEqual(process.name().casefold(), "conhost.exe")
                        expected[pid] = (process.create_time(), canonical_path(process.exe()))
                    self.assertEqual(snapshot["protected"], [])
                    if orphan:
                        (fixture / "exit-root").touch()
                        root.wait(timeout=5)
                    (fixture / "go").touch()
                    try:
                        _, err = worker.communicate(timeout=15)
                    except subprocess.TimeoutExpired:
                        self.fail((fixture / "trace.txt").read_text())
                    self.assertEqual(worker.returncode, 0, err.decode(errors="replace"))
                    results = json.loads((fixture / "result.json").read_text())
                    self.assertEqual(results[-1]["alive"], [], results)
                    self.assertEqual(results[-1]["denied"], [], results)
                    self.assertEqual(results[-1]["errors"], [], results)
                    killed = set().union(*(set(result["killed"]) for result in results))
                    wanted = script_pids - ({root.pid} if orphan else set())
                    self.assertTrue(wanted.issubset(killed), results)
                    self.assertTrue(killed.issubset(expected), results)
                    for pid, (created, path) in expected.items():
                        try:
                            remaining = psutil.Process(pid)
                            self.assertFalse(remaining.create_time() == created and canonical_path(remaining.exe()) == path)
                        except psutil.NoSuchProcess:
                            pass
                finally:
                    # Cleanup has the same identity check and only knows fixture
                    # PIDs; no process-name search or unrelated user app is used.
                    for pid, (created, path) in expected.items():
                        try:
                            process = psutil.Process(pid)
                            if process.create_time() == created and canonical_path(process.exe()) == path:
                                process.kill()
                                process.wait(timeout=5)
                        except psutil.NoSuchProcess:
                            pass
                    for process in processes:
                        if process.poll() is None:
                            process.kill()
                        process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()

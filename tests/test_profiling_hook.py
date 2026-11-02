"""Red tests for the champollion_utils profiling hook (pipeline TASK-221, PHOOK).

Design: cross-process-profiling component (COMP-ARCH-BDRABCZUK-7B43010AD32E),
Contracts 0, 1 and 2. Requirement ids are in the champollion_pipeline ledger
(area PROFILING); each test names the requirement title it verifies.

Every behavioural test runs its scenario in a fresh interpreter, so the pytest
process never gets a profiler, a fork hook or a changed environment. The
in-process tests import ``champollion_utils.profiling`` inside the test body,
so this module collects while the package does not exist yet.

User decisions of 2026-10-27 shape the scope: profiling is opt-in only, the
modes are ``cpu`` and ``light``, and GPU capture, non-Python children (the
tree sampler) and the report are other tasks.

User decisions of 2026-10-28 add the ScriptBuilder ``--profile DIR`` flag
(with ``--profile-mode {cpu,light}``), pin ``profile_file`` as a bare file name
next to its sidecar, and keep the ``--graphic-profiling`` name free for the
Nsight tasks. main.py forwarding of the flag is another task (PMAIN).
"""

import importlib
import json
import multiprocessing
import os
import pstats
import shutil
import signal
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from champollion_utils.script_builder import ScriptBuilder

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
STAGE = "demo-stage"
PROFILE_PREFIX = "CHAMPOLLION_PROFILE"

# Sidecar schema 1 keys of the cross-process-profiling component.
SIDECAR_KEYS = {
    "schema",
    "run_id",
    "host",
    "pid",
    "ppid",
    "seq",
    "parent_segment",
    "origin",
    "executable",
    "python_version",
    "argv",
    "cwd",
    "stage",
    "tags",
    "mode",
    "t_start",
    "t_end",
    "wall_s",
    "cpu_user_s",
    "cpu_sys_s",
    "children_cpu_user_s",
    "children_cpu_sys_s",
    "maxrss_kb",
    "complete",
    "exit_path",
    "profile_file",
    "mem_file",
    "gpu",
}

needs_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permissions",
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _profiling():
    """The profiling package; fails the test (not the collection) while it is absent."""
    return importlib.import_module("champollion_utils.profiling")


def _scrubbed_env(**extra: str) -> dict[str, str]:
    """os.environ without any CHAMPOLLION_PROFILE* key, src on PYTHONPATH, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(PROFILE_PREFIX)}
    env["PYTHONPATH"] = str(SRC_DIR)
    env["NO_COLOR"] = "1"
    env.update(extra)
    return env


def _script_builder_driver(tmp_path: Path, run_body: str, return_code: int = 0) -> Path:
    """Write a ScriptBuilder script whose run() executes `run_body`; return its path."""
    body = textwrap.indent(textwrap.dedent(run_body).strip() or "pass", " " * 8)
    source = (
        textwrap.dedent(
            """
            import os
            import signal
            import subprocess
            import sys

            from champollion_utils import script_builder
            from champollion_utils.script_builder import ScriptBuilder

            script_builder.check_for_updates = lambda *args, **kwargs: None


            def stage_work_marker_3e1():
                return sum(range(20000))


            class Demo(ScriptBuilder):
                def __init__(self):
                    super().__init__("STAGE", "profiling hook demo")

                def run(self):
            RUN_BODY
                    return RETURN_CODE


            if __name__ == "__main__":
                sys.exit(Demo().main())
            """
        )
        .replace("STAGE", STAGE)
        .replace("RETURN_CODE", str(return_code))
        .replace("RUN_BODY", body)
    )
    driver_dir = tmp_path / "driver"
    driver_dir.mkdir(exist_ok=True)
    driver = driver_dir / "demo_script.py"
    driver.write_text(source)
    return driver


def _plain_script(tmp_path: Path, name: str, source: str) -> Path:
    """Write a plain Python script (no champollion_utils import needed)."""
    driver_dir = tmp_path / "driver"
    driver_dir.mkdir(exist_ok=True)
    path = driver_dir / name
    path.write_text(textwrap.dedent(source))
    return path


def _run(
    script: Path,
    env: dict[str, str],
    cwd: Path,
    timeout: float = 120,
    args: tuple[str, ...] = (),
):
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _sidecar_paths(profile_dir: Path) -> list[Path]:
    return sorted(
        p for p in profile_dir.glob("*/proc/*.json") if not p.name.endswith(".mem.json")
    )


def _sidecars(profile_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in _sidecar_paths(profile_dir)]


def _segment_id(sidecar: dict) -> str:
    return f"{sidecar['host']}-{sidecar['pid']}-{sidecar['seq']}"


def _one(sidecars: list[dict], what: str, **match) -> dict:
    found = [s for s in sidecars if all(s.get(k) == v for k, v in match.items())]
    assert found, f"no sidecar for {what} ({match}); sidecars: {sidecars}"
    return found[0]


def _profile_path(profile_dir: Path, sidecar: dict) -> Path:
    """Resolve profile_file, absolute or relative to the sidecar's proc/ directory."""
    name = Path(sidecar["profile_file"])
    if name.is_absolute():
        return name
    return profile_dir / sidecar["run_id"] / "proc" / name


def _function_names(prof: Path) -> set[str]:
    stats = pstats.Stats(str(prof))
    return {func for (_file, _line, func) in stats.stats}


def _tree_files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def _profiled_demo(tmp_path: Path, run_body: str = "stage_work_marker_3e1()", **extra):
    """Run the demo ScriptBuilder with CHAMPOLLION_PROFILE exported; return (result, dir)."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    driver = _script_builder_driver(tmp_path, run_body)
    env = _scrubbed_env(CHAMPOLLION_PROFILE=str(profile_dir), **extra)
    result = _run(driver, env, tmp_path / "work")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result, profile_dir, driver


# --------------------------------------------------------------------------
# Profiling stays off without CHAMPOLLION_PROFILE (Contract 0, test 0a)
# --------------------------------------------------------------------------

DISABLED_ENVS = {
    "unset": {},
    "empty": {"CHAMPOLLION_PROFILE": ""},
    "mode-only": {"CHAMPOLLION_PROFILE_MODE": "cpu"},
    "every-other-key": {
        "CHAMPOLLION_PROFILE_MODE": "cpu",
        "CHAMPOLLION_PROFILE_GPU": "auto",
        "CHAMPOLLION_PROFILE_INTERVAL_S": "1.0",
        "CHAMPOLLION_PROFILE_TAGS": "subject=sub-01",
        "CHAMPOLLION_PROFILE_STAGE": "demo",
        "CHAMPOLLION_PROFILE_RUN_ID": "run-x",
    },
}


@pytest.mark.parametrize("case", sorted(DISABLED_ENVS))
def test_init_process_without_profile_dir_does_not_import_profiling(tmp_path, case):
    """Profiling stays off without CHAMPOLLION_PROFILE (guard: passes before the hook exists)."""
    probe = _plain_script(
        tmp_path,
        "probe.py",
        """
        import json
        import os
        import sys
        import threading

        import champollion_utils

        champollion_utils.init_process()
        print("PROBE=" + json.dumps({
            "imported": "champollion_utils.profiling" in sys.modules,
            "profiler": sys.getprofile() is not None,
            "pythonpath": os.environ.get("PYTHONPATH"),
            "threads": sorted(t.name for t in threading.enumerate()),
        }), flush=True)
        """,
    )
    env = _scrubbed_env(**DISABLED_ENVS[case])
    work = tmp_path / "work"
    result = _run(probe, env, work)
    assert result.returncode == 0, result.stderr
    line = next(x for x in result.stdout.splitlines() if x.startswith("PROBE="))
    probe_out = json.loads(line[len("PROBE=") :])
    assert probe_out["imported"] is False
    assert probe_out["profiler"] is False
    assert probe_out["pythonpath"] == env["PYTHONPATH"]
    assert "champollion-profile-sampler" not in probe_out["threads"]
    assert list(work.iterdir()) == []


# --------------------------------------------------------------------------
# ScriptBuilder writes a process sidecar; schema; complete flag; modes
# --------------------------------------------------------------------------


def test_script_builder_main_writes_sidecar_with_pid_ppid_argv_stage(tmp_path):
    """ScriptBuilder writes a process sidecar."""
    result, profile_dir, driver = _profiled_demo(
        tmp_path,
        "stage_work_marker_3e1()\nprint('PID=' + str(os.getpid()), flush=True)",
    )
    pid = int(next(x for x in result.stdout.splitlines() if x.startswith("PID="))[4:])
    paths = _sidecar_paths(profile_dir)
    assert paths, f"no sidecar under {profile_dir}/<run_id>/proc/"
    sidecar = _one(_sidecars(profile_dir), "the run() segment", stage=STAGE, pid=pid)
    assert sidecar["ppid"] == os.getpid()
    assert sidecar["argv"] == [str(driver)]
    run_dirs = {p.parent.parent.name for p in paths}
    assert run_dirs == {sidecar["run_id"]}


def test_sidecar_holds_schema_1_keys(tmp_path):
    """Sidecar schema 1 keys."""
    _result, profile_dir, _driver = _profiled_demo(tmp_path)
    sidecars = _sidecars(profile_dir)
    assert sidecars, "no sidecar written"
    for sidecar in sidecars:
        assert sidecar["schema"] == 1
        missing = SIDECAR_KEYS - set(sidecar)
        assert not missing, f"sidecar lacks {sorted(missing)}"


def test_sidecar_is_incomplete_when_process_is_killed_mid_segment(tmp_path):
    """Sidecar opened incomplete: a SIGKILL leaves complete false behind."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    driver = _script_builder_driver(tmp_path, "os.kill(os.getpid(), signal.SIGKILL)")
    env = _scrubbed_env(CHAMPOLLION_PROFILE=str(profile_dir))
    result = _run(driver, env, tmp_path / "work")
    assert result.returncode == -signal.SIGKILL, result.stderr
    sidecar = _one(_sidecars(profile_dir), "the killed run() segment", stage=STAGE)
    assert sidecar["complete"] is False


def test_sidecars_are_complete_after_normal_exit(tmp_path):
    """Sidecar completed on normal exit."""
    _result, profile_dir, _driver = _profiled_demo(tmp_path)
    sidecars = _sidecars(profile_dir)
    assert sidecars, "no sidecar written"
    assert [s["complete"] for s in sidecars] == [True] * len(sidecars)


def test_mode_cpu_writes_loadable_cprofile_dump(tmp_path):
    """Mode cpu writes a cProfile dump."""
    _result, profile_dir, _driver = _profiled_demo(
        tmp_path, CHAMPOLLION_PROFILE_MODE="cpu"
    )
    sidecar = _one(_sidecars(profile_dir), "the run() segment", stage=STAGE)
    assert sidecar["profile_file"], "profile_file is empty in mode cpu"
    prof = _profile_path(profile_dir, sidecar)
    assert prof.is_file(), prof
    assert "stage_work_marker_3e1" in _function_names(prof)


def test_mode_light_writes_no_cprofile_dump(tmp_path):
    """Mode light writes no cProfile dump."""
    _result, profile_dir, _driver = _profiled_demo(
        tmp_path, CHAMPOLLION_PROFILE_MODE="light"
    )
    sidecar = _one(_sidecars(profile_dir), "the run() segment", stage=STAGE)
    assert sidecar["mode"] == "light"
    assert sidecar["profile_file"] is None
    assert list(profile_dir.glob("*/proc/*.prof")) == []


def test_default_mode_is_cpu(tmp_path):
    """Default profiling mode is cpu."""
    _result, profile_dir, _driver = _profiled_demo(tmp_path)
    sidecar = _one(_sidecars(profile_dir), "the run() segment", stage=STAGE)
    assert sidecar["mode"] == "cpu"


@pytest.mark.parametrize("mode", ["mem", "both", "heavy", ""])
def test_make_profiling_env_rejects_other_modes(tmp_path, mode):
    """Only modes cpu and light."""
    profiling = _profiling()
    with pytest.raises(ValueError):
        profiling.make_profiling_env(tmp_path / "prof", mode, {})


@pytest.mark.parametrize("mode", ["cpu", "light"])
def test_make_profiling_env_accepts_cpu_and_light(tmp_path, mode):
    """Only modes cpu and light (the accepted side)."""
    profiling = _profiling()
    env = profiling.make_profiling_env(tmp_path / "prof", mode, {})
    assert env[profiling.ENV_PROFILE_DIR] == str(tmp_path / "prof")
    assert env[profiling.ENV_PROFILE_MODE] == mode


# --------------------------------------------------------------------------
# Python children of a profiled ScriptBuilder run()
# --------------------------------------------------------------------------

CHILD_SOURCE = """
import os
import sys

with open(sys.argv[1], "w") as handle:
    handle.write(str(os.getpid()))
"""


def _run_parent_with_child(tmp_path: Path):
    """A profiled ScriptBuilder whose run() starts a plain Python child; return ids."""
    child = _plain_script(tmp_path, "child.py", CHILD_SOURCE)
    pid_file = tmp_path / "child.pid"
    result, profile_dir, _driver = _profiled_demo(
        tmp_path,
        f"subprocess.run([sys.executable, {str(child)!r}, {str(pid_file)!r}], check=True)\n"
        "print('PID=' + str(os.getpid()), flush=True)",
    )
    parent_pid = int(
        next(x for x in result.stdout.splitlines() if x.startswith("PID="))[4:]
    )
    child_pid = int(pid_file.read_text())
    sidecars = _sidecars(profile_dir)
    parent = _one(sidecars, "the parent run() segment", stage=STAGE, pid=parent_pid)
    child_sidecar = _one(sidecars, "the Python child", pid=child_pid)
    return parent, child_sidecar


def test_child_sidecar_names_parent_run_segment(tmp_path):
    """Child sidecar names the parent segment."""
    parent, child = _run_parent_with_child(tmp_path)
    assert child["parent_segment"] == _segment_id(parent)
    assert child["ppid"] == parent["pid"]


def test_child_sidecar_inherits_parent_stage(tmp_path):
    """Child sidecar inherits the stage tag."""
    _parent, child = _run_parent_with_child(tmp_path)
    assert child["stage"] == STAGE


def test_child_without_champollion_utils_still_writes_sidecar(tmp_path):
    """Child profiled without champollion_utils (stand-in for the brainvisa envs)."""
    profiling = _profiling()
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "champollion_utils.py").write_text(
        'raise ImportError("champollion_utils blocked for this test")\n'
    )
    child = _plain_script(
        tmp_path,
        "child_no_utils.py",
        """
        import os
        try:
            import champollion_utils  # noqa: F401
        except ImportError:
            print("BLOCKED", flush=True)
        print("PID=" + str(os.getpid()), flush=True)
        """,
    )
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    base = _scrubbed_env(PYTHONPATH=str(shadow))
    env = profiling.make_profiling_env(profile_dir, "cpu", base)
    result = _run(child, env, tmp_path / "work")
    assert result.returncode == 0, result.stderr
    assert "BLOCKED" in result.stdout
    pid = int(next(x for x in result.stdout.splitlines() if x.startswith("PID="))[4:])
    sidecar = _one(
        _sidecars(profile_dir), "the child without champollion_utils", pid=pid
    )
    assert sidecar["complete"] is True


# --------------------------------------------------------------------------
# multiprocessing workers
# --------------------------------------------------------------------------

WORKERS_SOURCE = """
import json
import multiprocessing
import os
import sys


def worker_pid(_item):
    return os.getpid()


def process_target(queue):
    queue.put(os.getpid())


def parent_only_marker_9d2():
    return sum(range(50000))


if __name__ == "__main__":
    method, shape = sys.argv[1], sys.argv[2]
    for _ in range(20):
        parent_only_marker_9d2()
    ctx = multiprocessing.get_context(method)
    if shape == "pool-with":
        with ctx.Pool(2) as pool:
            pids = pool.map(worker_pid, range(8))
    elif shape == "pool-close-join":
        pool = ctx.Pool(2)
        pids = pool.map(worker_pid, range(8))
        pool.close()
        pool.join()
    else:
        queue = ctx.Queue()
        proc = ctx.Process(target=process_target, args=(queue,))
        proc.start()
        pids = [queue.get(timeout=60)]
        proc.join()
    print("WORKERS=" + json.dumps(sorted(set(pids))), flush=True)
"""


def _run_workers(tmp_path: Path, method: str, shape: str):
    profiling = _profiling()
    script = _plain_script(tmp_path, "workers.py", WORKERS_SOURCE)
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    env = profiling.make_profiling_env(profile_dir, "cpu", _scrubbed_env())
    work = tmp_path / "work"
    work.mkdir()
    result = subprocess.run(
        [sys.executable, str(script), method, shape],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    line = next(x for x in result.stdout.splitlines() if x.startswith("WORKERS="))
    return profile_dir, json.loads(line[len("WORKERS=") :])


@pytest.mark.parametrize("shape", ["pool-with", "pool-close-join", "process"])
@pytest.mark.parametrize("method", ["fork", "forkserver"])
def test_multiprocessing_worker_writes_complete_fork_sidecar(tmp_path, method, shape):
    """Forked worker writes a sidecar."""
    if method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"start method {method} unavailable on this platform")
    profile_dir, worker_pids = _run_workers(tmp_path, method, shape)
    sidecars = _sidecars(profile_dir)
    for pid in worker_pids:
        sidecar = _one(sidecars, f"worker {pid}", pid=pid)
        assert sidecar["origin"] == "fork"
        assert sidecar["complete"] is True


def test_forked_worker_profile_holds_no_parent_statistics(tmp_path):
    """Forked worker drops the parent profile."""
    profile_dir, worker_pids = _run_workers(tmp_path, "fork", "pool-close-join")
    sidecars = _sidecars(profile_dir)
    for pid in worker_pids:
        sidecar = _one(sidecars, f"worker {pid}", pid=pid)
        assert sidecar["profile_file"], f"worker {pid} has no profile_file"
        names = _function_names(_profile_path(profile_dir, sidecar))
        assert "parent_only_marker_9d2" not in names


# --------------------------------------------------------------------------
# SIGTERM, sitecustomize chaining, failures, stdout
# --------------------------------------------------------------------------


def test_sigterm_completes_sidecar_then_terminates(tmp_path):
    """SIGTERM completes the sidecar."""
    profiling = _profiling()
    ready = tmp_path / "ready"
    sleeper = _plain_script(
        tmp_path,
        "sleeper.py",
        f"""
        import time
        open({str(ready)!r}, "w").close()
        time.sleep(60)
        """,
    )
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    env = profiling.make_profiling_env(profile_dir, "cpu", _scrubbed_env())
    proc = subprocess.Popen(
        [sys.executable, str(sleeper)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "sleeper never started"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == -signal.SIGTERM
    sidecar = _one(_sidecars(profile_dir), "the terminated process", pid=proc.pid)
    assert sidecar["complete"] is True
    assert sidecar["exit_path"] == "sigterm"


def test_boot_sitecustomize_chains_to_later_sitecustomize(tmp_path):
    """Boot sitecustomize chains."""
    profiling = _profiling()
    other = tmp_path / "other_site"
    other.mkdir()
    (other / "sitecustomize.py").write_text(
        'import os\nos.environ["CHAINED_SITECUSTOMIZE_7A"] = "ran"\n'
    )
    probe = _plain_script(
        tmp_path,
        "chain_probe.py",
        """
        import os
        print("PID=" + str(os.getpid()), flush=True)
        print("CHAINED=" + os.environ.get("CHAINED_SITECUSTOMIZE_7A", "missing"), flush=True)
        """,
    )
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    env = profiling.make_profiling_env(
        profile_dir, "light", _scrubbed_env(PYTHONPATH=str(other))
    )
    entries = env["PYTHONPATH"].split(os.pathsep)
    assert str(other) in entries[1:], entries
    result = _run(probe, env, tmp_path / "work")
    assert result.returncode == 0, result.stderr
    assert "CHAINED=ran" in result.stdout
    pid = int(next(x for x in result.stdout.splitlines() if x.startswith("PID="))[4:])
    _one(_sidecars(profile_dir), "the probe (boot ran first)", pid=pid)


@needs_non_root
def test_unwritable_profile_dir_logs_one_warning_and_keeps_exit_status(tmp_path):
    """Unwritable profile directory."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    driver = _script_builder_driver(tmp_path, "stage_work_marker_3e1()", return_code=3)
    env = _scrubbed_env(CHAMPOLLION_PROFILE=str(profile_dir))
    profile_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = _run(driver, env, tmp_path / "work")
    finally:
        profile_dir.chmod(stat.S_IRWXU)
    assert result.returncode == 3, result.stderr
    warnings = [
        line
        for line in (result.stdout + result.stderr).splitlines()
        if " WARNING " in line and "profil" in line.lower()
    ]
    assert len(warnings) == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_profiled_script_stdout_matches_unprofiled(tmp_path):
    """Profiled stdout unchanged."""
    body = "stage_work_marker_3e1()\nprint('stage-output-line', flush=True)"
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    plain_driver = _script_builder_driver(plain_dir, body)
    plain = _run(plain_driver, _scrubbed_env(), plain_dir / "work")
    assert plain.returncode == 0, plain.stderr
    profiled, profile_dir, _driver = _profiled_demo(tmp_path, body)
    assert _sidecars(profile_dir), "profiled run wrote no sidecar"
    assert profiled.stdout == plain.stdout


def test_make_profiling_env_boot_dir_is_writable_and_under_run_dir(tmp_path):
    """Boot directory under the run directory (container: not the read-only code dir)."""
    profiling = _profiling()
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    env = profiling.make_profiling_env(profile_dir, "cpu", {"PYTHONPATH": "/x/y"})
    entries = env["PYTHONPATH"].split(os.pathsep)
    boot = Path(entries[0])
    assert boot.resolve().is_relative_to(profile_dir.resolve()), boot
    assert (boot / "sitecustomize.py").is_file()
    assert os.access(boot, os.W_OK)
    assert entries[1:] == ["/x/y"]


@needs_non_root
def test_read_only_install_profiles_into_profile_dir(tmp_path):
    """Read-only install profiles (container: code bind mounted read-only)."""
    code = tmp_path / "code"
    shutil.copytree(
        SRC_DIR / "champollion_utils",
        code / "champollion_utils",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    before = _tree_files(code)
    paths = [code, *code.rglob("*")]
    for path in paths:
        path.chmod(0o555 if path.is_dir() else 0o444)
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    driver = _script_builder_driver(tmp_path, "stage_work_marker_3e1()")
    env = _scrubbed_env(PYTHONPATH=str(code), CHAMPOLLION_PROFILE=str(profile_dir))
    try:
        result = _run(driver, env, tmp_path / "work")
    finally:
        for path in paths:
            path.chmod(0o755 if path.is_dir() else 0o644)
    assert result.returncode == 0, result.stderr
    assert _sidecars(profile_dir), "no sidecar under CHAMPOLLION_PROFILE"
    assert _tree_files(code) == before


# --------------------------------------------------------------------------
# ScriptBuilder --profile DIR and --profile-mode (user decisions 2026-10-28)
# --------------------------------------------------------------------------

RUN_MARKER = "RUN-BODY-RAN-5c8"


def _parser_demo() -> ScriptBuilder:
    """An in-process ScriptBuilder subclass for parser-level checks."""

    class ParserDemo(ScriptBuilder):
        def __init__(self):
            super().__init__(STAGE, "profiling flag demo")

        def run(self):
            return 0

    return ParserDemo()


def _profiling_warnings(result) -> list[str]:
    return [
        line
        for line in (result.stdout + result.stderr).splitlines()
        if " WARNING " in line and "profil" in line.lower()
    ]


def _sidecar_items(profile_dir: Path) -> list[tuple[Path, dict]]:
    return [(p, json.loads(p.read_text())) for p in _sidecar_paths(profile_dir)]


def _value_line(stdout: str, key: str) -> str:
    prefix = key + "="
    return next(x for x in stdout.splitlines() if x.startswith(prefix))[len(prefix) :]


def _flag_run(
    tmp_path: Path,
    args: list[str],
    run_body: str = "stage_work_marker_3e1()",
    return_code: int = 0,
    **extra: str,
):
    """Run the demo ScriptBuilder with `args` in tmp_path/work and a scrubbed env."""
    driver = _script_builder_driver(tmp_path, run_body, return_code)
    return _run(driver, _scrubbed_env(**extra), tmp_path / "work", args=tuple(args))


def test_script_builder_parser_accepts_profile_dir(tmp_path):
    """ScriptBuilder accepts --profile DIR (also next to the legacy --profiling flag)."""
    target = str(tmp_path / "prof")
    assert _parser_demo().parse_args(["--profile", target]).profile == target
    legacy = _parser_demo().add_flag("--profiling", "legacy cProfile flag")
    args = legacy.parse_args(["--profiling", "--profile", target])
    assert args.profile == target
    assert args.profiling is True
    with pytest.raises(SystemExit):
        _parser_demo().parse_args(["--profile"])


def test_profile_flag_writes_process_sidecar_under_dir(tmp_path):
    """--profile writes the process sidecar."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    result = _flag_run(
        tmp_path,
        ["--profile", str(profile_dir)],
        "stage_work_marker_3e1()\nprint('PID=' + str(os.getpid()), flush=True)",
    )
    assert result.returncode == 0, result.stderr
    pid = int(_value_line(result.stdout, "PID"))
    items = _sidecar_items(profile_dir)
    found = [
        (p, s) for p, s in items if s.get("pid") == pid and s.get("stage") == STAGE
    ]
    assert found, f"no process sidecar under {profile_dir}/<run_id>/proc/: {items}"
    path, sidecar = found[0]
    assert path.parent == profile_dir / sidecar["run_id"] / "proc"


@pytest.mark.parametrize("inherited", ["unset", "other-dir"])
def test_profile_flag_sets_absolute_profile_env_for_run(tmp_path, inherited):
    """--profile exports CHAMPOLLION_PROFILE (absolute, replacing an inherited value)."""
    work = tmp_path / "work"
    (work / "prof").mkdir(parents=True)
    extra = {}
    if inherited == "other-dir":
        (tmp_path / "other").mkdir()
        extra["CHAMPOLLION_PROFILE"] = str(tmp_path / "other")
    result = _flag_run(
        tmp_path,
        ["--profile", "prof"],
        "print('PROFILE_ENV=' + os.environ.get('CHAMPOLLION_PROFILE', ''), flush=True)",
        **extra,
    )
    assert result.returncode == 0, result.stderr
    value = Path(_value_line(result.stdout, "PROFILE_ENV"))
    assert value.is_absolute(), value
    assert value.resolve() == (work / "prof").resolve()


def test_profile_flag_profiles_python_child_in_other_cwd(tmp_path):
    """--profile profiles Python children (relative DIR, child in another cwd)."""
    work = tmp_path / "work"
    profile_dir = work / "prof"
    profile_dir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    child = _plain_script(tmp_path, "child.py", CHILD_SOURCE)
    pid_file = tmp_path / "child.pid"
    result = _flag_run(
        tmp_path,
        ["--profile", "prof"],
        f"subprocess.run([sys.executable, {str(child)!r}, {str(pid_file)!r}],"
        f" cwd={str(elsewhere)!r}, check=True)\n"
        "print('PID=' + str(os.getpid()), flush=True)",
    )
    assert result.returncode == 0, result.stderr
    parent_pid = int(_value_line(result.stdout, "PID"))
    child_pid = int(pid_file.read_text())
    items = _sidecar_items(profile_dir)
    sidecars = [s for _p, s in items]
    parent = _one(sidecars, "the parent run() segment", stage=STAGE, pid=parent_pid)
    child_path, child_sidecar = next(
        ((p, s) for p, s in items if s.get("pid") == child_pid), (None, None)
    )
    assert child_sidecar, f"no child sidecar under {profile_dir}: {sidecars}"
    assert child_sidecar["run_id"] == parent["run_id"]
    assert child_path.parent == profile_dir / parent["run_id"] / "proc"
    assert list(elsewhere.iterdir()) == []


def test_script_builder_without_profile_flag_does_not_import_profiling(tmp_path):
    """No --profile keeps profiling off (guard: passes before the hook exists)."""
    result = _flag_run(
        tmp_path,
        [],
        """
        import json
        print("PROBE=" + json.dumps({
            "imported": "champollion_utils.profiling" in sys.modules,
            "profiler": sys.getprofile() is not None,
            "env": sorted(k for k in os.environ if k.startswith("CHAMPOLLION_PROFILE")),
        }), flush=True)
        """,
    )
    assert result.returncode == 0, result.stderr
    probe = json.loads(_value_line(result.stdout, "PROBE"))
    assert probe == {"imported": False, "profiler": False, "env": []}
    assert list((tmp_path / "work").iterdir()) == []


def test_print_args_without_profile_flag_shows_no_profiling_option(capsys):
    """print_args hides absent profiling options (guard: passes before the flag exists)."""
    builder = _parser_demo()
    builder.parse_args([])
    builder.print_args()
    assert "profil" not in capsys.readouterr().out.lower()


def test_profile_flag_creates_missing_dir_under_existing_parent(tmp_path):
    """--profile creates the directory."""
    target = tmp_path / "new-prof"
    result = _flag_run(tmp_path, ["--profile", str(target)])
    assert result.returncode == 0, result.stderr
    assert target.is_dir()


def test_profile_flag_missing_parent_logs_one_warning_and_keeps_exit_status(tmp_path):
    """--profile with a missing parent."""
    target = tmp_path / "missing" / "prof"
    result = _flag_run(tmp_path, ["--profile", str(target)], return_code=3)
    assert result.returncode == 3, result.stderr
    assert len(_profiling_warnings(result)) == 1, result.stdout + result.stderr
    assert not (tmp_path / "missing").exists()


def test_profile_flag_file_path_logs_one_warning_and_keeps_exit_status(tmp_path):
    """--profile naming a file."""
    target = tmp_path / "prof"
    target.write_text("not a directory\n")
    result = _flag_run(tmp_path, ["--profile", str(target)], return_code=3)
    assert result.returncode == 3, result.stderr
    assert len(_profiling_warnings(result)) == 1, result.stdout + result.stderr
    assert target.read_text() == "not a directory\n"


@needs_non_root
def test_profile_flag_unwritable_dir_logs_one_warning_and_keeps_exit_status(tmp_path):
    """--profile naming an unwritable directory."""
    target = tmp_path / "prof"
    target.mkdir()
    target.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = _flag_run(tmp_path, ["--profile", str(target)], return_code=3)
    finally:
        target.chmod(stat.S_IRWXU)
    assert result.returncode == 3, result.stderr
    assert len(_profiling_warnings(result)) == 1, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["mem", "both", "heavy", ""])
def test_profile_mode_flag_rejects_other_modes(tmp_path, mode):
    """--profile-mode accepts only cpu and light."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    result = _flag_run(
        tmp_path,
        ["--profile", str(profile_dir), "--profile-mode", mode],
        f"print({RUN_MARKER!r}, flush=True)",
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "unrecognized arguments" not in result.stderr, result.stderr
    assert "--profile-mode" in result.stderr
    assert RUN_MARKER not in result.stdout


def test_profile_mode_flag_without_profile_flag_is_rejected(tmp_path):
    """--profile-mode requires --profile."""
    result = _flag_run(
        tmp_path, ["--profile-mode", "cpu"], f"print({RUN_MARKER!r}, flush=True)"
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "unrecognized arguments" not in result.stderr, result.stderr
    assert "--profile" in result.stderr
    assert RUN_MARKER not in result.stdout


@pytest.mark.parametrize("mode", ["cpu", "light"])
def test_profile_mode_flag_sets_mode_env_for_run(tmp_path, mode):
    """--profile-mode exports the mode (replacing an inherited value)."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    inherited = "light" if mode == "cpu" else "cpu"
    result = _flag_run(
        tmp_path,
        ["--profile", str(profile_dir), "--profile-mode", mode],
        "print('MODE_ENV=' + os.environ.get('CHAMPOLLION_PROFILE_MODE', ''), flush=True)",
        CHAMPOLLION_PROFILE_MODE=inherited,
    )
    assert result.returncode == 0, result.stderr
    assert _value_line(result.stdout, "MODE_ENV") == mode


def test_profile_mode_flag_light_recorded_in_sidecar(tmp_path):
    """--profile-mode light recorded."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    result = _flag_run(
        tmp_path, ["--profile", str(profile_dir), "--profile-mode", "light"]
    )
    assert result.returncode == 0, result.stderr
    sidecar = _one(_sidecars(profile_dir), "the run() segment", stage=STAGE)
    assert sidecar["mode"] == "light"


@pytest.mark.parametrize("mode", ["mem", "both", "heavy", ""])
def test_invalid_profile_mode_env_logs_one_warning_and_writes_no_sidecar(
    tmp_path, mode
):
    """Invalid CHAMPOLLION_PROFILE_MODE value: one WARNING, run without profiling."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    result = _flag_run(
        tmp_path,
        [],
        return_code=3,
        CHAMPOLLION_PROFILE=str(profile_dir),
        CHAMPOLLION_PROFILE_MODE=mode,
    )
    assert result.returncode == 3, result.stderr
    assert len(_profiling_warnings(result)) == 1, result.stdout + result.stderr
    assert _sidecar_paths(profile_dir) == []


def test_profile_file_is_bare_name_next_to_sidecar(tmp_path):
    """profile_file is a bare name next to the sidecar (PAGG fixtures assume it)."""
    _result, profile_dir, _driver = _profiled_demo(
        tmp_path, CHAMPOLLION_PROFILE_MODE="cpu"
    )
    named = [(p, s) for p, s in _sidecar_items(profile_dir) if s["profile_file"]]
    assert named, "no sidecar names a profile_file in mode cpu"
    for path, sidecar in named:
        name = sidecar["profile_file"]
        assert Path(name).name == name, f"{path.name}: profile_file {name!r}"
        assert (path.parent / name).is_file(), f"{name} not next to {path}"


@pytest.mark.parametrize("source", ["flag", "env"])
def test_script_builder_boot_dir_is_first_on_pythonpath_under_run_dir(tmp_path, source):
    """Boot directory under the profile run directory (not the code dir)."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    body = "print('PYPATH=' + os.environ.get('PYTHONPATH', ''), flush=True)"
    if source == "flag":
        result = _flag_run(tmp_path, ["--profile", str(profile_dir)], body)
    else:
        result = _flag_run(tmp_path, [], body, CHAMPOLLION_PROFILE=str(profile_dir))
    assert result.returncode == 0, result.stderr
    boot = Path(_value_line(result.stdout, "PYPATH").split(os.pathsep)[0])
    sidecar = _one(_sidecars(profile_dir), "the run() segment", stage=STAGE)
    run_dir = profile_dir / sidecar["run_id"]
    assert boot.resolve().is_relative_to(run_dir.resolve()), boot
    assert (boot / "sitecustomize.py").is_file()
    assert os.access(boot, os.W_OK)


def test_script_builder_leaves_graphic_profiling_flag_free():
    """--graphic-profiling name left free for the Nsight tasks (guard)."""
    builder = _parser_demo()
    builder.add_flag("--graphic-profiling", "declared by a GPU script")
    assert builder.parse_args(["--graphic-profiling"]).graphic_profiling is True


def test_resolve_mode_absent_key_is_cpu():
    """Default profiling mode is cpu: only an absent CHAMPOLLION_PROFILE_MODE means the default."""
    assert _profiling().resolve_mode(None) == "cpu"


@pytest.mark.parametrize("value", ["", "mem", "CPU"])
def test_resolve_mode_rejects_other_values(value):
    """Invalid CHAMPOLLION_PROFILE_MODE value (the empty string included, user decision 2026-10-28)."""
    profiling = _profiling()
    with pytest.raises(profiling.InvalidProfileMode):
        profiling.resolve_mode(value)


def test_script_builder_profile_modes_match_profiling_modes():
    """ScriptBuilder's literal --profile-mode choices stay equal to profiling.MODES."""
    from champollion_utils import script_builder

    assert script_builder.PROFILE_MODES == _profiling().MODES

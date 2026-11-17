"""Characterisation tests for champollion_utils.profiling (core.py and __init__.py).

Task TASK-PROFILING-BDRABCZUK-687854868D08 (utils ledger, area PROFILING). The
code already exists: these tests pin its current public behaviour, one test per
Requirement; each docstring names the Requirement title it verifies.

Pure helpers run in the pytest process. Every test that starts a profiling
session runs its scenario in a fresh interpreter, so the pytest process never
gets a profiler, a fork hook, a SIGTERM handler or a changed environment.
"""

import importlib
import json
import os
import pstats
import re
import signal
import socket
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
CORE_PATH = SRC_DIR / "champollion_utils" / "profiling" / "core.py"
PROFILE_PREFIX = "CHAMPOLLION_PROFILE"
BOOT_MODULE_NAME = "_champollion_profiling_core"

ENV_NAMES = {
    "ENV_PROFILE_DIR": "CHAMPOLLION_PROFILE",
    "ENV_PROFILE_MODE": "CHAMPOLLION_PROFILE_MODE",
    "ENV_PROFILE_RUN": "CHAMPOLLION_PROFILE_RUN_ID",
    "ENV_PROFILE_STAGE": "CHAMPOLLION_PROFILE_STAGE",
    "ENV_PROFILE_TAGS": "CHAMPOLLION_PROFILE_TAGS",
    "ENV_PROFILE_PARENT": "CHAMPOLLION_PROFILE_PARENT",
    "ENV_PROFILE_SAMPLER": "CHAMPOLLION_PROFILE_SAMPLER_PID",
    "ENV_PROFILE_INTERVAL": "CHAMPOLLION_PROFILE_INTERVAL_S",
    "ENV_PROFILE_GPU": "CHAMPOLLION_PROFILE_GPU",
}

EXPORTED_NAMES = [
    "ENV_PROFILE_DIR",
    "ENV_PROFILE_GPU",
    "ENV_PROFILE_INTERVAL",
    "ENV_PROFILE_MODE",
    "ENV_PROFILE_PARENT",
    "ENV_PROFILE_RUN",
    "ENV_PROFILE_SAMPLER",
    "ENV_PROFILE_STAGE",
    "ENV_PROFILE_TAGS",
    "LOGGER_NAME",
    "MODES",
    "InvalidProfileMode",
    "ensure_profiling_from_env",
    "get_boot_dir",
    "is_profiling_enabled",
    "make_profiling_env",
    "new_run_id",
    "profile_segment",
    "resolve_mode",
    "write_boot_dir",
]

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

HOST = socket.gethostname()
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-" + re.escape(HOST) + r"-\d+$")

# Collects the WARNING records of every logger, printed as JSON on the last line.
WARNING_COLLECTOR = """
import logging

class _Collect(logging.Handler):
    records = []

    def emit(self, record):
        if record.levelno == logging.WARNING:
            self.records.append(record.name)

logging.getLogger().addHandler(_Collect())
"""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _profiling():
    return importlib.import_module("champollion_utils.profiling")


def _env(**extra: str) -> dict[str, str]:
    """os.environ without CHAMPOLLION_PROFILE* keys, src on PYTHONPATH, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(PROFILE_PREFIX)}
    env["PYTHONPATH"] = str(SRC_DIR)
    env.update(extra)
    return env


def _script(tmp_path: Path, source: str, name: str = "probe.py") -> Path:
    driver_dir = tmp_path / "driver"
    driver_dir.mkdir(exist_ok=True)
    path = driver_dir / name
    path.write_text(textwrap.dedent(source))
    return path


def _run(script: Path, env: dict[str, str], cwd: Path, *flags: str):
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, *flags, str(script)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _profiled(tmp_path: Path, body: str, name: str = "probe.py", **extra: str):
    """Run `body` (with `p` = champollion_utils.profiling) under CHAMPOLLION_PROFILE."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir(exist_ok=True)
    source = (
        "import json, os, sys\n"
        "import champollion_utils.profiling as p\n" + textwrap.dedent(body)
    )
    script = _script(tmp_path, source, name)
    env = _env(CHAMPOLLION_PROFILE=str(profile_dir), **extra)
    result = _run(script, env, tmp_path / "work")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result, profile_dir


def _sidecars(profile_dir: Path) -> list[dict]:
    paths = sorted(profile_dir.glob("*/proc/*.json"))
    return sorted(
        (json.loads(path.read_text()) for path in paths),
        key=lambda sidecar: (sidecar["pid"], sidecar["seq"]),
    )


def _only_sidecar(profile_dir: Path) -> dict:
    sidecars = _sidecars(profile_dir)
    assert len(sidecars) == 1, sidecars
    return sidecars[0]


def _last_json(stdout: str):
    return json.loads(stdout.strip().splitlines()[-1])


def _tree(root: Path) -> set[str]:
    return {str(path.relative_to(root)) for path in root.rglob("*")}


# --------------------------------------------------------------------------
# Public names of champollion_utils.profiling
# --------------------------------------------------------------------------


def test_env_variable_names():
    """Profiling env variable names."""
    profiling = _profiling()
    assert {name: getattr(profiling, name) for name in ENV_NAMES} == ENV_NAMES


def test_modes_constant():
    """Profiling modes constant."""
    assert _profiling().MODES == ("cpu", "light")


def test_logger_name_constant():
    """Profiling logger name constant."""
    assert _profiling().LOGGER_NAME == "champollion_utils.profiling"


def test_package_export_list():
    """Profiling package export list."""
    assert sorted(_profiling().__all__) == sorted(EXPORTED_NAMES)
    assert len(_profiling().__all__) == len(EXPORTED_NAMES)


def test_package_names_are_core_objects():
    """Profiling package re-exports the core objects."""
    profiling = _profiling()
    core = sys.modules["champollion_utils.profiling.core"]
    for name in profiling.__all__:
        assert getattr(profiling, name) is getattr(core, name), name


def test_package_reuses_boot_loaded_core(tmp_path):
    """Profiling package reuses the boot-loaded core."""
    script = _script(
        tmp_path,
        f"""
        import importlib.util, json, sys

        spec = importlib.util.spec_from_file_location({BOOT_MODULE_NAME!r}, {str(CORE_PATH)!r})
        boot = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = boot
        spec.loader.exec_module(boot)

        import champollion_utils.profiling as p

        print(json.dumps({{
            "core": sys.modules["champollion_utils.profiling.core"] is boot,
            "names": [n for n in p.__all__ if getattr(p, n) is not getattr(boot, n)],
        }}))
        """,
    )
    result = _run(script, _env(), tmp_path / "work")
    assert result.returncode == 0, result.stderr
    assert _last_json(result.stdout) == {"core": True, "names": []}


# --------------------------------------------------------------------------
# resolve_mode, InvalidProfileMode, new_run_id
# --------------------------------------------------------------------------


def test_resolve_mode_none_is_cpu():
    """resolve_mode of an absent value is cpu."""
    assert _profiling().resolve_mode(None) == "cpu"


@pytest.mark.parametrize("mode", ["cpu", "light"])
def test_resolve_mode_keeps_known_mode(mode):
    """resolve_mode keeps a known mode."""
    assert _profiling().resolve_mode(mode) == mode


@pytest.mark.parametrize("value", ["", "CPU", "heavy", "memory", " cpu"])
def test_resolve_mode_rejects_unknown_mode(value):
    """resolve_mode rejects an unknown mode."""
    profiling = _profiling()
    with pytest.raises(profiling.InvalidProfileMode):
        profiling.resolve_mode(value)


def test_invalid_profile_mode_is_value_error():
    """InvalidProfileMode is a ValueError."""
    assert issubclass(_profiling().InvalidProfileMode, ValueError)


def test_new_run_id_format():
    """new_run_id format."""
    fmt = "%Y%m%dT%H%M%SZ"
    before = datetime.now(timezone.utc).strftime(fmt)
    run_id = _profiling().new_run_id()
    after = datetime.now(timezone.utc).strftime(fmt)
    expected = {f"{stamp}-{HOST}-{os.getpid()}" for stamp in (before, after)}
    assert run_id in expected


# --------------------------------------------------------------------------
# Boot dir helpers
# --------------------------------------------------------------------------


def test_get_boot_dir_location(tmp_path):
    """get_boot_dir location."""
    assert _profiling().get_boot_dir(tmp_path / "run") == tmp_path / "run" / "boot"


def test_write_boot_dir_creates_boot_file(tmp_path):
    """write_boot_dir creates the boot file."""
    run_dir = tmp_path / "missing" / "run"
    _profiling().write_boot_dir(run_dir)
    assert (run_dir / "boot" / "sitecustomize.py").is_file()


def test_write_boot_dir_returns_boot_dir(tmp_path):
    """write_boot_dir returns the boot dir."""
    run_dir = tmp_path / "run"
    assert _profiling().write_boot_dir(run_dir) == run_dir / "boot"


def test_write_boot_dir_keeps_existing_boot_file(tmp_path):
    """write_boot_dir keeps an existing boot file."""
    boot = tmp_path / "run" / "boot"
    boot.mkdir(parents=True)
    (boot / "sitecustomize.py").write_text("# kept\n")
    _profiling().write_boot_dir(tmp_path / "run")
    assert (boot / "sitecustomize.py").read_text() == "# kept\n"


# --------------------------------------------------------------------------
# make_profiling_env
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["", "CPU", "heavy", "memory"])
def test_make_profiling_env_rejects_unknown_mode(tmp_path, mode):
    """make_profiling_env rejects an unknown mode."""
    profiling = _profiling()
    with pytest.raises(profiling.InvalidProfileMode):
        profiling.make_profiling_env(tmp_path / "prof", mode, {})


def test_make_profiling_env_leaves_base_env_unchanged(tmp_path):
    """make_profiling_env leaves base_env unchanged."""
    base = {"PYTHONPATH": "/a", "OTHER": "x"}
    snapshot = dict(base)
    env = _profiling().make_profiling_env(tmp_path / "prof", "cpu", base)
    assert base == snapshot
    assert env is not base


def test_make_profiling_env_sets_absolute_profile_dir(tmp_path, monkeypatch):
    """make_profiling_env sets the absolute profile dir."""
    monkeypatch.chdir(tmp_path)
    env = _profiling().make_profiling_env(Path("prof"), "cpu", {})
    assert env["CHAMPOLLION_PROFILE"] == str(tmp_path / "prof")


@pytest.mark.parametrize("mode", ["cpu", "light"])
def test_make_profiling_env_sets_mode(tmp_path, mode):
    """make_profiling_env sets the mode."""
    env = _profiling().make_profiling_env(tmp_path / "prof", mode, {})
    assert env["CHAMPOLLION_PROFILE_MODE"] == mode


def test_make_profiling_env_keeps_inherited_run_id(tmp_path):
    """make_profiling_env keeps an inherited run id."""
    base = {"CHAMPOLLION_PROFILE_RUN_ID": "run-inherited"}
    env = _profiling().make_profiling_env(tmp_path / "prof", "cpu", base)
    assert env["CHAMPOLLION_PROFILE_RUN_ID"] == "run-inherited"


@pytest.mark.parametrize("base", [{}, {"CHAMPOLLION_PROFILE_RUN_ID": ""}])
def test_make_profiling_env_creates_run_id(tmp_path, base):
    """make_profiling_env creates a run id."""
    env = _profiling().make_profiling_env(tmp_path / "prof", "cpu", base)
    assert RUN_ID_RE.match(env["CHAMPOLLION_PROFILE_RUN_ID"])


def test_make_profiling_env_puts_boot_dir_first(tmp_path):
    """make_profiling_env puts the boot dir first on PYTHONPATH."""
    base = {
        "CHAMPOLLION_PROFILE_RUN_ID": "run-x",
        "PYTHONPATH": os.pathsep.join(["/a", "/b"]),
    }
    env = _profiling().make_profiling_env(tmp_path / "prof", "cpu", base)
    boot = tmp_path / "prof" / "run-x" / "boot"
    assert env["PYTHONPATH"].split(os.pathsep) == [str(boot), "/a", "/b"]


# --------------------------------------------------------------------------
# ensure_profiling_from_env and is_profiling_enabled
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, ""])
def test_ensure_stays_off_without_profile_dir(tmp_path, value):
    """ensure_profiling_from_env stays off without a profile dir."""
    script = _script(
        tmp_path,
        """
        import json
        import champollion_utils.profiling as p
        print(json.dumps(p.ensure_profiling_from_env()))
        """,
    )
    extra = {} if value is None else {"CHAMPOLLION_PROFILE": value}
    before = _tree(tmp_path)
    result = _run(script, _env(CHAMPOLLION_PROFILE_MODE="cpu", **extra), tmp_path)
    assert result.returncode == 0, result.stderr
    assert _last_json(result.stdout) is False
    assert _tree(tmp_path) == before


def test_is_profiling_enabled_true_after_start(tmp_path):
    """is_profiling_enabled is True in a profiled process."""
    result, _ = _profiled(
        tmp_path,
        """
        started = p.ensure_profiling_from_env()
        print(json.dumps([started, p.is_profiling_enabled()]))
        """,
    )
    assert _last_json(result.stdout) == [True, True]


def test_is_profiling_enabled_false_when_off(tmp_path):
    """is_profiling_enabled is False without a profile dir."""
    script = _script(
        tmp_path,
        """
        import json
        import champollion_utils.profiling as p
        started = p.ensure_profiling_from_env()
        print(json.dumps([started, p.is_profiling_enabled()]))
        """,
    )
    result = _run(script, _env(), tmp_path / "work")
    assert result.returncode == 0, result.stderr
    assert _last_json(result.stdout) == [False, False]


def test_ensure_is_idempotent(tmp_path):
    """ensure_profiling_from_env is idempotent."""
    result, profile_dir = _profiled(
        tmp_path,
        """
        first = p.ensure_profiling_from_env(stage="one")
        second = p.ensure_profiling_from_env(stage="two")
        print(json.dumps([first, second]))
        """,
    )
    assert _last_json(result.stdout) == [True, True]
    assert [s["stage"] for s in _sidecars(profile_dir)] == ["one"]


def test_invalid_mode_env_disables_profiling(tmp_path):
    """Invalid profile mode env disables profiling."""
    result, profile_dir = _profiled(
        tmp_path,
        "print(json.dumps(p.ensure_profiling_from_env()))\n",
        CHAMPOLLION_PROFILE_MODE="heavy",
    )
    assert _last_json(result.stdout) is False
    assert _sidecars(profile_dir) == []


def test_invalid_mode_env_logs_one_warning(tmp_path):
    """Invalid profile mode env logs one warning."""
    result, _ = _profiled(
        tmp_path,
        WARNING_COLLECTOR
        + """
p.ensure_profiling_from_env()
p.ensure_profiling_from_env()
print(json.dumps(_Collect.records))
""",
        CHAMPOLLION_PROFILE_MODE="heavy",
    )
    assert _last_json(result.stdout) == ["champollion_utils.profiling"]


def _file_as_profile_dir(tmp_path: Path, body: str):
    """Run `body` with CHAMPOLLION_PROFILE naming a regular file."""
    not_a_dir = tmp_path / "not_a_dir"
    not_a_dir.write_text("")
    source = "import json\nimport champollion_utils.profiling as p\n" + body
    script = _script(tmp_path, source)
    result = _run(script, _env(CHAMPOLLION_PROFILE=str(not_a_dir)), tmp_path / "work")
    assert result.returncode == 0, result.stderr
    return _last_json(result.stdout)


def test_unwritable_profile_dir_disables_profiling(tmp_path):
    """Unwritable profile dir disables profiling."""
    body = "print(json.dumps(p.ensure_profiling_from_env()))\n"
    assert _file_as_profile_dir(tmp_path, body) is False


def test_unwritable_profile_dir_logs_one_warning(tmp_path):
    """Unwritable profile dir logs one warning."""
    body = WARNING_COLLECTOR + (
        "p.ensure_profiling_from_env()\n"
        "p.ensure_profiling_from_env()\n"
        "print(json.dumps(_Collect.records))\n"
    )
    assert _file_as_profile_dir(tmp_path, body) == ["champollion_utils.profiling"]


# --------------------------------------------------------------------------
# Run id and run.json
# --------------------------------------------------------------------------


def test_root_process_exports_new_run_id(tmp_path):
    """Root process exports a new run id."""
    result, profile_dir = _profiled(
        tmp_path,
        """
        p.ensure_profiling_from_env()
        print(json.dumps(os.environ.get(p.ENV_PROFILE_RUN)))
        """,
    )
    run_id = _last_json(result.stdout)
    assert RUN_ID_RE.match(run_id), run_id
    assert (profile_dir / run_id).is_dir()


def test_root_process_writes_run_json(tmp_path):
    """Root process writes run.json."""
    result, profile_dir = _profiled(
        tmp_path,
        """
        p.ensure_profiling_from_env()
        print(json.dumps(os.environ[p.ENV_PROFILE_RUN]))
        """,
    )
    run_id = _last_json(result.stdout)
    run_json = json.loads((profile_dir / run_id / "run.json").read_text())
    assert run_json["schema"] == 1
    assert run_json["run_id"] == run_id
    assert run_json["t_end"] is not None


def test_inherited_run_id_joins_run(tmp_path):
    """Process with an inherited run id joins the run."""
    _, profile_dir = _profiled(
        tmp_path,
        "p.ensure_profiling_from_env()\n",
        CHAMPOLLION_PROFILE_RUN_ID="run-inherited",
    )
    sidecars = list((profile_dir / "run-inherited" / "proc").glob("*.json"))
    assert len(sidecars) == 1
    assert json.loads(sidecars[0].read_text())["run_id"] == "run-inherited"


def test_inherited_run_id_writes_no_run_json(tmp_path):
    """Process with an inherited run id writes no run.json."""
    _, profile_dir = _profiled(
        tmp_path,
        "p.ensure_profiling_from_env()\n",
        CHAMPOLLION_PROFILE_RUN_ID="run-inherited",
    )
    assert not (profile_dir / "run-inherited" / "run.json").exists()


# --------------------------------------------------------------------------
# Segment stage, tags and parent
# --------------------------------------------------------------------------


def test_segment_stage_from_argument(tmp_path):
    """Segment stage from the stage argument."""
    _, profile_dir = _profiled(
        tmp_path,
        "p.ensure_profiling_from_env(stage='explicit')\n",
        CHAMPOLLION_PROFILE_STAGE="from-env",
    )
    assert _only_sidecar(profile_dir)["stage"] == "explicit"


def test_segment_stage_from_env(tmp_path):
    """Segment stage from CHAMPOLLION_PROFILE_STAGE."""
    _, profile_dir = _profiled(
        tmp_path,
        "p.ensure_profiling_from_env()\n",
        CHAMPOLLION_PROFILE_STAGE="from-env",
    )
    assert _only_sidecar(profile_dir)["stage"] == "from-env"


def test_segment_stage_from_script_name(tmp_path):
    """Segment stage from the script name."""
    _, profile_dir = _profiled(
        tmp_path, "p.ensure_profiling_from_env()\n", name="stage_probe_x.py"
    )
    assert _only_sidecar(profile_dir)["stage"] == "stage_probe_x.py"


def test_segment_tags_from_env(tmp_path):
    """Segment tags from CHAMPOLLION_PROFILE_TAGS."""
    _, profile_dir = _profiled(
        tmp_path,
        "p.ensure_profiling_from_env()\n",
        CHAMPOLLION_PROFILE_TAGS="subject=sub-01, bad ,=x, session = ses-1",
    )
    tags = _only_sidecar(profile_dir)["tags"]
    assert tags == {"subject": "sub-01", "session": "ses-1"}


def test_segment_parent_from_env(tmp_path):
    """Segment parent from CHAMPOLLION_PROFILE_PARENT."""
    _, profile_dir = _profiled(
        tmp_path,
        "p.ensure_profiling_from_env()\n",
        CHAMPOLLION_PROFILE_PARENT="otherhost-1234-2",
    )
    assert _only_sidecar(profile_dir)["parent_segment"] == "otherhost-1234-2"


def test_open_segment_exported_as_parent(tmp_path):
    """Open segment exported as parent for children."""
    result, _ = _profiled(
        tmp_path,
        """
        p.ensure_profiling_from_env()
        print(json.dumps([os.getpid(), os.environ.get(p.ENV_PROFILE_PARENT)]))
        """,
    )
    pid, parent = _last_json(result.stdout)
    assert parent == f"{HOST}-{pid}-0"


def test_open_segment_stage_exported(tmp_path):
    """Open segment stage exported for children."""
    result, _ = _profiled(
        tmp_path,
        """
        p.ensure_profiling_from_env(stage="exported")
        print(json.dumps(os.environ.get(p.ENV_PROFILE_STAGE)))
        """,
    )
    assert _last_json(result.stdout) == "exported"


# --------------------------------------------------------------------------
# Sidecars and cProfile dumps
# --------------------------------------------------------------------------

READ_OPEN_SIDECAR = """
p.ensure_profiling_from_env(stage="open")
run_dir = os.path.join(os.environ[p.ENV_PROFILE_DIR], os.environ[p.ENV_PROFILE_RUN])
name = os.environ[p.ENV_PROFILE_PARENT] + ".json"
with open(os.path.join(run_dir, "proc", name)) as handle:
    print(json.dumps({"name": name, "sidecar": json.load(handle)}))
"""


def test_sidecar_written_incomplete_at_open(tmp_path):
    """Sidecar written incomplete when a segment opens."""
    result, _ = _profiled(tmp_path, READ_OPEN_SIDECAR)
    seen = _last_json(result.stdout)
    sidecar = seen["sidecar"]
    assert seen["name"] == f"{HOST}-{sidecar['pid']}-{sidecar['seq']}.json"
    assert sidecar["complete"] is False


def test_sidecar_holds_schema_1_keys(tmp_path):
    """Sidecar holds the schema 1 keys."""
    result, profile_dir = _profiled(tmp_path, READ_OPEN_SIDECAR)
    for sidecar in (_last_json(result.stdout)["sidecar"], _only_sidecar(profile_dir)):
        assert set(sidecar) == SIDECAR_KEYS
        assert sidecar["schema"] == 1


def test_sidecar_completed_at_normal_exit(tmp_path):
    """Sidecar completed at normal exit."""
    _, profile_dir = _profiled(tmp_path, "p.ensure_profiling_from_env()\n")
    sidecar = _only_sidecar(profile_dir)
    assert sidecar["complete"] is True
    assert sidecar["exit_path"] == "atexit"


def test_mode_cpu_writes_cprofile_dump(tmp_path):
    """Mode cpu writes a cProfile dump."""
    _, profile_dir = _profiled(
        tmp_path,
        "p.ensure_profiling_from_env()\nsum(range(1000))\n",
        CHAMPOLLION_PROFILE_MODE="cpu",
    )
    sidecar = _only_sidecar(profile_dir)
    name = f"{sidecar['host']}-{sidecar['pid']}-{sidecar['seq']}"
    assert sidecar["profile_file"] == f"{name}.prof"
    dump = profile_dir / sidecar["run_id"] / "proc" / sidecar["profile_file"]
    assert pstats.Stats(str(dump)).stats


def test_mode_light_sidecar_names_no_profile_file(tmp_path):
    """Mode light sidecar names no profile file."""
    _, profile_dir = _profiled(
        tmp_path,
        "p.ensure_profiling_from_env()\n",
        CHAMPOLLION_PROFILE_MODE="light",
    )
    assert _only_sidecar(profile_dir)["profile_file"] is None


def test_mode_light_writes_no_cprofile_dump(tmp_path):
    """Mode light writes no cProfile dump."""
    _, profile_dir = _profiled(
        tmp_path,
        "p.ensure_profiling_from_env()\n",
        CHAMPOLLION_PROFILE_MODE="light",
    )
    assert _sidecars(profile_dir)
    assert list(profile_dir.glob("*/proc/*.prof")) == []


# --------------------------------------------------------------------------
# SIGTERM and fork
# --------------------------------------------------------------------------


def _terminated(tmp_path: Path):
    """Start a profiled sleeper, SIGTERM it once ready; return (returncode, dir)."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    script = _script(
        tmp_path,
        """
        import time
        import champollion_utils.profiling as p
        p.ensure_profiling_from_env(stage="sleeper")
        print("ready", flush=True)
        time.sleep(60)
        """,
    )
    (tmp_path / "work").mkdir()
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=tmp_path / "work",
        env=_env(CHAMPOLLION_PROFILE=str(profile_dir)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "ready"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()
    return proc.returncode, profile_dir


def test_sigterm_completes_sidecar(tmp_path):
    """SIGTERM completes the sidecar."""
    _, profile_dir = _terminated(tmp_path)
    sidecar = _only_sidecar(profile_dir)
    assert sidecar["complete"] is True
    assert sidecar["exit_path"] == "sigterm"


def test_sigterm_still_ends_process(tmp_path):
    """SIGTERM still ends the process."""
    returncode, _ = _terminated(tmp_path)
    assert returncode == -signal.SIGTERM


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_forked_child_opens_own_segment(tmp_path):
    """Forked child opens its own segment."""
    result, profile_dir = _profiled(
        tmp_path,
        """
        p.ensure_profiling_from_env(stage="forker")
        child = os.fork()
        if child == 0:
            os._exit(0)
        os.waitpid(child, 0)
        print(json.dumps([os.getpid(), child]))
        """,
    )
    parent_pid, child_pid = _last_json(result.stdout)
    child = [s for s in _sidecars(profile_dir) if s["pid"] == child_pid]
    assert len(child) == 1, _sidecars(profile_dir)
    assert child[0]["origin"] == "fork"
    assert child[0]["parent_segment"] == f"{HOST}-{parent_pid}-0"


# --------------------------------------------------------------------------
# profile_segment
# --------------------------------------------------------------------------


def test_profile_segment_off_runs_block_only(tmp_path):
    """profile_segment without profiling runs the block only."""
    script = _script(
        tmp_path,
        """
        import champollion_utils.profiling as p
        p.ensure_profiling_from_env()
        with p.profile_segment("inner", {"k": "v"}):
            print("ran")
        """,
    )
    before = _tree(tmp_path)
    result = _run(script, _env(), tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ran"
    assert _tree(tmp_path) == before


NESTED_SEGMENT = """
p.ensure_profiling_from_env(stage="outer")
with p.profile_segment("inner", {"k": "v"}):
    sum(range(1000))
"""


def test_profile_segment_opens_new_segment(tmp_path):
    """profile_segment opens a segment for a new stage."""
    _, profile_dir = _profiled(tmp_path, NESTED_SEGMENT)
    stages = [(s["seq"], s["stage"], s["tags"]) for s in _sidecars(profile_dir)]
    assert stages[1] == (1, "inner", {"k": "v"})


def test_profile_segment_resumes_previous_stage(tmp_path):
    """profile_segment resumes the previous stage."""
    _, profile_dir = _profiled(tmp_path, NESTED_SEGMENT)
    stages = [(s["seq"], s["stage"], s["tags"]) for s in _sidecars(profile_dir)]
    assert stages == [(0, "outer", {}), (1, "inner", {"k": "v"}), (2, "outer", {})]


def test_profile_segment_keeps_same_stage(tmp_path):
    """profile_segment keeps a segment of the same stage."""
    _, profile_dir = _profiled(
        tmp_path,
        """
        p.ensure_profiling_from_env(stage="outer")
        with p.profile_segment("outer"):
            sum(range(1000))
        """,
    )
    assert [s["stage"] for s in _sidecars(profile_dir)] == ["outer"]


# --------------------------------------------------------------------------
# Output, isolation and the boot dir
# --------------------------------------------------------------------------


def test_profiling_writes_nothing_on_stdout(tmp_path):
    """Profiling writes nothing on stdout."""
    body = """
    import champollion_utils.profiling as p
    print("before")
    p.ensure_profiling_from_env(stage="talk")
    with p.profile_segment("inner"):
        print("inside")
    print("after")
    """
    script = _script(tmp_path, body)
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    plain = _run(script, _env(), tmp_path / "plain")
    profiled = _run(script, _env(CHAMPOLLION_PROFILE=str(profile_dir)), tmp_path / "w")
    assert plain.returncode == profiled.returncode == 0, profiled.stderr
    assert _sidecars(profile_dir)
    assert profiled.stdout == plain.stdout


def test_core_runs_without_champollion_utils(tmp_path):
    """Profiling core runs without champollion_utils."""
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    script = _script(
        tmp_path,
        f"""
        import importlib.abc, importlib.util, json, sys

        class _Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "champollion_utils":
                    raise ImportError("champollion_utils blocked")
                return None

        sys.meta_path.insert(0, _Block())
        spec = importlib.util.spec_from_file_location({BOOT_MODULE_NAME!r}, {str(CORE_PATH)!r})
        core = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = core
        spec.loader.exec_module(core)
        print(json.dumps(core.ensure_profiling_from_env(stage="isolated")))
        """,
    )
    env = _env(CHAMPOLLION_PROFILE=str(profile_dir))
    del env["PYTHONPATH"]
    result = _run(script, env, tmp_path / "work", "-I")
    assert result.returncode == 0, result.stderr
    assert _last_json(result.stdout) is True
    assert _only_sidecar(profile_dir)["stage"] == "isolated"


def test_boot_dir_profiles_python_child(tmp_path):
    """Boot dir profiles a Python child."""
    profile_dir = tmp_path / "prof"
    base = _env()
    del base["PYTHONPATH"]
    env = _profiling().make_profiling_env(profile_dir, "light", base)
    assert str(SRC_DIR) not in env["PYTHONPATH"].split(os.pathsep)
    child = _script(tmp_path, "x = 1\n", "plain_child.py")
    result = _run(child, env, tmp_path / "work")
    assert result.returncode == 0, result.stderr
    run_id = env["CHAMPOLLION_PROFILE_RUN_ID"]
    sidecars = list((profile_dir / run_id / "proc").glob("*.json"))
    assert len(sidecars) == 1
    assert json.loads(sidecars[0].read_text())["stage"] == "plain_child.py"


def test_boot_file_chains_to_later_sitecustomize(tmp_path):
    """Boot file chains to a later sitecustomize."""
    other = tmp_path / "other"
    other.mkdir()
    marker = tmp_path / "chained.txt"
    (other / "sitecustomize.py").write_text(
        f"open({str(marker)!r}, 'w').write('chained')\n"
    )
    base = _env(PYTHONPATH=str(other))
    env = _profiling().make_profiling_env(tmp_path / "prof", "light", base)
    child = _script(tmp_path, "x = 1\n", "plain_child.py")
    result = _run(child, env, tmp_path / "work")
    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "chained"

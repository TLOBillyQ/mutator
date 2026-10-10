"""Run a project's tests, and decide the command for each language."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from mutator.crapper_link import ensure_crapper


@dataclass(frozen=True)
class CommandResult:
    code: int
    timed_out: bool
    seconds: float
    output: str


if os.name == "nt":
    import ctypes

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_ulong),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_ulong),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_ulong),
            ("SchedulingClass", ctypes.c_ulong),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("basic", _BasicLimit),
            ("io", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def _windows_job(process: subprocess.Popen) -> int | None:
    """A kill-on-close Job Object holding the process and its descendants.

    Windows has no process groups, so a shell command times out by terminating
    a Job: every descendant joins it at spawn, and none outlives the handle.
    """

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    handle = getattr(process, "_handle", None)
    if not job or handle is None:
        if job:
            kernel32.CloseHandle(job)
        return None
    info = _ExtendedLimitInformation()
    info.basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    sized = ctypes.sizeof(info)
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), sized):
        kernel32.CloseHandle(job)
        return None
    if not kernel32.AssignProcessToJobObject(job, handle):
        kernel32.CloseHandle(job)
        return None
    return job


def _windows_kill(pid: int, job: int | None) -> None:
    """Terminate the tree rooted at the shell command.

    taskkill walks the parent chain, so it must run while the tree is intact:
    it reaches descendants that broke away from the Job (the venv python.exe
    redirector re-executes the real interpreter outside it). Terminating the
    Job afterwards cleans up anything taskkill missed.
    """

    import ctypes

    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    if job:
        ctypes.windll.kernel32.TerminateJobObject(job, 124)


def _worker_home(cwd: Path) -> Path | None:
    """The overlay root when cwd is inside target/mutation-workers."""

    for candidate in [cwd, *cwd.parents]:
        parent = candidate.parent
        if not candidate.name.startswith("worker-"):
            continue
        if not parent.name.startswith("run-"):
            continue
        if parent.parent.name == "mutation-workers":
            return candidate
    return None


def _source_entries(cwd: Path, worker: Path) -> list[str]:
    found = []
    for directory in (cwd / "src", cwd, worker / "src", worker):
        text = str(directory)
        if text in found:
            continue
        if directory.is_dir():
            found.append(text)
    return found


def _prefer_worker_sources(cwd: Path, environment: dict) -> None:
    """An editable install would otherwise import the unmutated tree."""

    worker = _worker_home(cwd)
    if worker is None:
        return
    entries = _source_entries(cwd, worker)
    current = environment.get("PYTHONPATH")
    if current:
        entries.append(current)
    if not entries:
        return
    environment["PYTHONPATH"] = os.pathsep.join(entries)


class CommandRunner:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def run(self, command: str, cwd: Path, timeout: float | None) -> CommandResult:
        if self.verbose:
            print(f"+ ({cwd}) {command}", file=sys.stderr)
        started = time.monotonic()
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        _prefer_worker_sources(cwd, environment)
        process = subprocess.Popen(
            command,
            cwd=cwd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
            env=environment,
        )
        job = _windows_job(process) if os.name == "nt" else None
        try:
            output, _err = process.communicate(timeout=timeout)
            code = process.returncode if process.returncode is not None else 1
            timed_out = False
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                _windows_kill(process.pid, job)
            output, _err = process.communicate()
            code = 124
            timed_out = True
        finally:
            if job:
                import ctypes

                ctypes.windll.kernel32.CloseHandle(job)
        seconds = time.monotonic() - started
        return CommandResult(code=code, timed_out=timed_out, seconds=seconds, output=output or "")


def nearest(start: Path, marker: str, stop: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    stop = stop.resolve()
    current = current.resolve()
    while True:
        if (current / marker).is_file():
            return current
        if current == stop or current.parent == current:
            return None
        current = current.parent


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _clojure_command(directory: Path) -> str:
    deps = directory / "deps.edn"
    bb = directory / "bb.edn"
    if bb.is_file() and not deps.is_file():
        text = _read(bb)
        if "spec" in text:
            return "bb spec --tag ~no-mutate"
        return "bb test"
    text = _read(deps)
    if ":spec" in text and "speclj" in text:
        return "clj -M:spec --tag ~no-mutate"
    if ":spec" in text:
        return "clj -M:spec"
    return "clj -M:test"


def _project_interpreter(directory: Path) -> str:
    """The project's virtualenv, or this process when the project has none.

    The path stays absolute and the symlink is left in place. A worker does
    not link ``.venv``, and resolving ``bin/python`` would leave the virtualenv.
    """

    for name in (".venv", "venv"):
        candidate = (directory / name / "bin" / "python").absolute()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable


def _python_command(directory: Path) -> str:
    interpreter = _project_interpreter(directory)
    pyproject = _read(directory / "pyproject.toml")
    if (
        (directory / "pytest.ini").is_file()
        or (directory / "conftest.py").is_file()
        or "pytest" in pyproject
        or (directory / "setup.cfg").is_file() and "pytest" in _read(directory / "setup.cfg")
    ):
        return f"{interpreter} -m pytest"
    return f"{interpreter} -m unittest discover"


def test_plan(root: Path, source: Path, language: str, override: str | None) -> tuple[str, Path]:
    """The test command and the directory it runs in."""

    root = root.resolve()
    if override:
        return override, root
    if language == "clojure":
        directory = nearest(source, "deps.edn", root) or nearest(source, "bb.edn", root) or root
        return _clojure_command(directory), directory
    if language == "java":
        directory = nearest(source, "pom.xml", root) or root
        return "mvn -q test -DexcludeTags=no-mutate", directory
    if language == "go":
        directory = nearest(source, "go.mod", root) or root
        relative = source.resolve().parent.relative_to(directory.resolve())
        package = "." if relative == Path(".") else "./" + relative.as_posix()
        return f"go test -count=1 {package}", directory
    if language == "typescript":
        directory = nearest(source, "package.json", root) or root
        return "npm test", directory
    if language == "rust":
        directory = nearest(source, "Cargo.toml", root) or root
        return "cargo test", directory
    if language == "python":
        directory = (
            nearest(source, "pyproject.toml", root)
            or nearest(source, "pytest.ini", root)
            or nearest(source, "setup.cfg", root)
            or root
        )
        return _python_command(directory), directory
    if language == "lua":
        return _lua_plan(root, source)
    return "false", root


def _lua_plan(root: Path, source: Path) -> tuple[str, Path]:
    """busted in the nearest `.busted` or rockspec directory, on Lua 5.4."""

    runners = ensure_crapper().runners
    directory = runners.lua_roots(root, [source])[0]
    lua = runners.lua_interpreter() or "lua5.4"
    return f"busted --lua={shlex.quote(lua)}", directory

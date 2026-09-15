"""Private JSONL transport for a process that reuses a single lane module.

The coordinator owns scheduling. Each worker runs one case at a time, with
fresh main() locals and runner-owned resource cleanup. No conversation is reused.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any


class PersistentRunner:
    def __init__(self, repo_root: Path) -> None:
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=repo_root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )

    def run(self, command: list[str], root: Path) -> dict[str, Any]:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps({"command": command, "root": str(root)}) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("persistent runner exited without a receipt")
        return json.loads(line)

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.process.stdout is not None:
            self.process.stdout.close()


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    modules: dict[str, Any] = {}
    # A separate descriptor keeps native writes and runner prints out of IPC.
    protocol = os.fdopen(os.dup(1), "w", buffering=1)
    saved_stdout, saved_stderr = os.dup(1), os.dup(2)
    for line in sys.stdin:
        job = json.loads(line)
        command, root = job["command"], Path(job["root"])
        root.mkdir(parents=True, exist_ok=True)
        paths = [root / f"runner.{stream}.private.log" for stream in ("stdout", "stderr")]
        handles = [os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600) for path in paths]
        started = time.time()
        clock = time.perf_counter()
        setup_ms = None
        code = 0
        try:
            os.dup2(handles[0], 1)
            os.dup2(handles[1], 2)
            script = command[1]
            sys.argv = command[1:]
            if script not in modules:
                name = "_persistent_lane_" + hashlib.sha256(script.encode()).hexdigest()[:16]
                spec = importlib.util.spec_from_file_location(name, script)
                if spec is None or spec.loader is None:
                    raise ImportError("lane module unavailable")
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                modules[script] = module
            setup_ms = (time.perf_counter() - clock) * 1000
            code = int(modules[script].main() or 0)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        except BaseException:
            traceback.print_exc()
            code = 1
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            for handle in handles:
                os.close(handle)
        protocol.write(json.dumps({
            "runner_exit_code": code, "runner_pid": os.getpid(),
            "runner_started_unix_s": started, "runner_finished_unix_s": time.time(),
            "runner_wall_ms": (time.perf_counter() - clock) * 1000,
            "runner_setup_ms": setup_ms,
        }) + "\n")
    protocol.close()
    os.close(saved_stdout)
    os.close(saved_stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

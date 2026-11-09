"""父进程看护：AstrBot 退出/被杀时，把中间层连同子进程一起收掉。

插件拉起中间层时会带环境变量 ``CHORUS_PARENT_PID=<AstrBot 的 pid>``。这里每 2 秒
看一眼父进程还在不在：不在了就 ``taskkill /PID <自己> /T /F``，把 app.py 和常驻
worker 一起结束，避免留下占着 3333 端口和显存的孤儿进程。

为什么不在插件里直接杀：AstrBot 如果是被任务管理器强杀、或者机器睡眠后进程消失，
插件里的清理代码根本没有机会执行；只有被拉起的进程自己盯着父进程才可靠。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time

INTERVAL_SECONDS = 2.0


def _parent_alive(pid: int) -> bool:
    """父进程还在不在。Windows 上用 WaitForSingleObject，别用 os.kill（会真杀进程）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill_self_tree() -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(os.getpid()), "/T", "/F"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    os._exit(0)


def watch(parent_pid: int, interval: float = INTERVAL_SECONDS) -> None:
    def loop() -> None:
        while True:
            time.sleep(interval)
            if not _parent_alive(parent_pid):
                print(
                    "[chorus-mw] 父进程 %d 已退出，停止中间层（含常驻 worker）" % parent_pid,
                    flush=True,
                )
                _kill_self_tree()

    threading.Thread(target=loop, name="parent-watch", daemon=True).start()


def start_from_env() -> bool:
    """有 CHORUS_PARENT_PID 就开看护，返回是否开启。"""
    raw = (os.environ.get("CHORUS_PARENT_PID") or "").strip()
    if not raw:
        return False
    try:
        pid = int(raw)
    except ValueError:
        return False
    if pid <= 0 or pid == os.getpid():
        return False
    watch(pid)
    print("[chorus-mw] 已开启父进程看护：pid=%d 退出后自动停止" % pid, flush=True)
    return True

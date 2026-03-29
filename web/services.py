"""System service management — systemctl, git, metrics."""

import asyncio
import os

import psutil


# ── Standalone functions (no settings dependency) ────────

async def service_status(name: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "--user", "is-active", name,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    state = stdout.decode().strip()
    return {"name": name, "active": state == "active", "status": state}


async def restart_service(name: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "--user", "restart", name,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {"code": proc.returncode, "stdout": stdout.decode(), "stderr": stderr.decode()}


async def stop_service(name: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "--user", "stop", name,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {"code": proc.returncode, "stdout": stdout.decode(), "stderr": stderr.decode()}


async def stream_logs(service_name: str, lines: int = 50):
    """Async generator: yield log lines from journalctl -f."""
    proc = await asyncio.create_subprocess_exec(
        "journalctl", "--user", "-u", service_name, "-f", "--no-pager", f"-n{lines}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        async for line in proc.stdout:
            yield line.decode().rstrip("\n")
    finally:
        proc.terminate()
        await proc.wait()


def system_metrics() -> dict:
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    load = psutil.getloadavg()
    return {
        "cpu_percent": cpu,
        "memory_total": mem.total,
        "memory_used": mem.used,
        "memory_percent": mem.percent,
        "disk_total": disk.total,
        "disk_used": disk.used,
        "disk_percent": disk.percent,
        "load_1m": load[0],
        "load_5m": load[1],
        "load_15m": load[2],
    }


def format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ── Settings-dependent functions (factory) ───────────────

def make_services(settings, bot_main_file: str):
    """Create settings-bound service functions.

    Returns a dict with: SERVICES, all_service_statuses, git_pull, run_update.
    """
    services_map = {
        "cli": settings.service_cli,
        "telegram": settings.service_tg,
        "web": settings.service_web,
    }

    async def all_service_statuses() -> list[dict]:
        results = []
        for label, name in services_map.items():
            s = await service_status(name)
            s["label"] = label
            results.append(s)
        return results

    async def git_pull() -> dict:
        repo = settings.repo_dir
        if not os.path.isdir(repo):
            return {"code": 1, "stdout": "", "stderr": f"Repo dir not found: {repo}"}
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo, "pull", "--ff-only",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return {"code": proc.returncode, "stdout": stdout.decode(), "stderr": stderr.decode()}

    async def run_update() -> list[dict]:
        """Full update: git pull -> copy files -> restart TG service."""
        results = []

        pull = await git_pull()
        results.append({"step": "git_pull", **pull})
        if pull["code"] != 0:
            return results

        install = settings.install_dir
        repo = settings.repo_dir
        if os.path.isdir(install) and os.path.isdir(repo):
            copy_cmds = [
                f"cp {repo}/{bot_main_file} {install}/{bot_main_file}",
                f"cp {repo}/requirements.txt {install}/requirements.txt",
                f"mkdir -p {install}/core {install}/tg",
                f"cp {repo}/core/*.py {install}/core/",
                f"cp {repo}/tg/*.py {install}/tg/",
            ]
            proc = await asyncio.create_subprocess_shell(
                " && ".join(copy_cmds),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            results.append({"step": "copy_files", "code": proc.returncode,
                            "stdout": stdout.decode(), "stderr": stderr.decode()})

        st = await service_status(settings.service_tg)
        if st["active"]:
            r = await restart_service(settings.service_tg)
            results.append({"step": "restart_tg", **r})

        return results

    return {
        "SERVICES": services_map,
        "all_service_statuses": all_service_statuses,
        "git_pull": git_pull,
        "run_update": run_update,
    }

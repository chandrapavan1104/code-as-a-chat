import psutil
from server.skills.base import Skill
from server.skills import register


class SysmonSkill(Skill):
    name = "sysmon"
    description = "System monitor: CPU, RAM, disk usage, and top processes"

    async def run(self, prompt: str = "", **kwargs) -> str:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        procs: list[dict] = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                procs.append(p.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        top5 = sorted(procs, key=lambda x: x.get("cpu_percent") or 0, reverse=True)[:5]

        lines = [
            f"CPU:  {cpu}%",
            f"RAM:  {mem.percent}%  ({mem.used / 1024**3:.1f} GB / {mem.total / 1024**3:.1f} GB)",
            f"Disk: {disk.percent}%  ({disk.used / 1024**3:.1f} GB / {disk.total / 1024**3:.1f} GB)",
            "",
            "Top Processes (by CPU):",
        ]
        for p in top5:
            cpu_pct = p.get("cpu_percent") or 0
            mem_pct = p.get("memory_percent") or 0
            lines.append(f"  [{p['pid']:>6}]  {p['name']:<28}  CPU {cpu_pct:>5.1f}%  RAM {mem_pct:.1f}%")

        battery = psutil.sensors_battery()
        if battery is not None:
            plug = "charging" if battery.power_plugged else "on battery"
            lines.append(f"\nBattery: {battery.percent:.0f}%  ({plug})")

        return "\n".join(lines)


register(SysmonSkill())

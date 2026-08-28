"""Lightweight GPU/hardware audit (observational only)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess


class HardwareAudit:
    """Collect a compact machine and GPU summary for agent context."""

    def collect(self, *, timeout: int = 20) -> dict:
        """Return a structured summary without configuring CUDA or scheduling GPUs."""
        return {
            "os": platform.platform(),
            "cpu_cores": os.cpu_count() or 0,
            "gpus": self._gpus(timeout),
        }

    def text(self, *, timeout: int = 20) -> str:
        """Render the collected summary as a single prompt-safe string."""
        info = self.collect(timeout=timeout)
        gpus = info["gpus"]
        gpu_line = "GPU: none visible" if not gpus else "GPU:\n" + "\n".join(gpus)
        return f"OS: {info['os']}\nCPU cores: {info['cpu_cores']}\n{gpu_line}"

    @staticmethod
    def _gpus(timeout: int) -> list[str]:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return []
        try:
            result = subprocess.run(
                [
                    exe,
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

"""Worker specializing in safe file creation and bounded Python execution."""
from pathlib import Path
from typing import Any

from .base import BaseWorker
from .tools import code_tools


class CodeAgent(BaseWorker):
    def __init__(self, model: Any, workspace: str | Path | None = None, *, timeout: float = 10.0):
        super().__init__("code_agent", model, code_tools(workspace, timeout=timeout))
        self.system_prompt = (
            "You are a Code Generation Specialist. Create or update code only within the assigned workspace. "
            "Run relevant scripts with the bounded run_python tool before reporting completion. "
            "Describe created files and test output accurately."
        )

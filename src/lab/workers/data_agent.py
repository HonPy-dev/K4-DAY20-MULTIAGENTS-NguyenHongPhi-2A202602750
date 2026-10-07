"""Worker specializing in local tabular data inspection and validation."""
from pathlib import Path
from typing import Any

from .base import BaseWorker
from .tools import data_tools


class DataAgent(BaseWorker):
    def __init__(self, model: Any, workspace: str | Path | None = None):
        super().__init__("data_agent", model, data_tools(workspace))
        self.system_prompt = (
            "You are a Data Analysis Specialist. Inspect and validate local CSV/JSON data using the available tools. "
            "Explain assumptions, summarize relevant findings, and return concise insights rather than dumping raw data. "
            "Do not claim calculations or validation that you did not perform."
        )

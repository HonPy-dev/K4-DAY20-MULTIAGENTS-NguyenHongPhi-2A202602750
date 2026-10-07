"""Worker specializing in structured quality evaluation."""
from typing import Any

from .base import BaseWorker
from .tools import evaluator_tools


class EvaluatorAgent(BaseWorker):
    def __init__(self, model: Any):
        super().__init__("evaluator_agent", model, evaluator_tools())
        self.system_prompt = (
            "You are a Quality Evaluation Specialist. Evaluate supplied results for accuracy, completeness, clarity, "
            "and performance. Use available scoring, validation, comparison, and report tools when appropriate. Return a JSON object with "
            "score (0-100), feedback, issues, and suggestions; distinguish verified facts from assumptions."
        )

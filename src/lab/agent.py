"""GUIDE Phần 1 - Dựng tác tử (agent) bằng Deep Agents.   >>> SINH VIÊN CÀI ĐẶT make_backend VÀ build_agent <<<

Pseudo-code: guides/pseudocode/01_agent.md
Kiểm tra:    pytest tests/test_02_agent.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from .model import make_model
from .subagents import get_subagents

# ---- CÓ SẴN, KHÔNG SỬA: system prompt dùng chung cho mọi sinh viên (để đường cơ sở so sánh được) ----
PATHS_NOTE = (
    "PATHS: every path is relative to the sandbox root and never starts with '/'. "
    "The task files are in the folder workspace/ (for example workspace/app.log). "
    "Use exactly this relative form both in the file tools and in the shell (execute); "
    "the shell starts in the sandbox root. "
)
BASE_PROMPT = (
    "You are an engineering assistant working in a sandbox. "
    + PATHS_NOTE
    + "Use the shell to run Python and tests. "
    "When you are done, reply with a short summary that mentions only files you really created or changed."
)
SKILLS_NOTE = (
    " Skills are in the folder skills/ (one sub-folder per skill with a SKILL.md). "
    "As your FIRST action, read the SKILL.md of every skill whose description could apply to the task, "
    "then follow them. Never modify skills/."
)
SUBAGENTS_NOTE = (
    " You have specialised subagents (see the description of the task tool). "
    "For anything beyond a trivial step, delegate to a suitable subagent and put ALL the task rules and file paths "
    "in the delegation message, because a subagent sees only what you send. "
    "Check what a subagent returns before you rely on it."
)
# --------------------------------------------------------------------------------------------------


class _WindowsBashBackend(LocalShellBackend):
    """Run POSIX task commands through Git Bash when this lab is tested on native Windows.

    The lab itself targets macOS/Linux (or WSL/Docker). This narrow adapter keeps the same
    LocalShellBackend filesystem behavior while allowing the offline tests' POSIX commands
    (`which`, `cat`, and `ls`) to work on a Windows development machine.
    """

    def __init__(self, *args, bash_path: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._bash_path = bash_path

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if not command or not isinstance(command, str):
            return ExecuteResponse(output="Error: Command must be a non-empty string.", exit_code=1, truncated=False)
        effective_timeout = timeout if timeout is not None else self._default_timeout
        if effective_timeout <= 0:
            raise ValueError(f"timeout must be positive, got {effective_timeout}")
        try:
            proc = subprocess.Popen(
                [self._bash_path, "-c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                env=self._env,
                cwd=str(self.cwd),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            try:
                stdout, stderr = proc.communicate(timeout=effective_timeout)
            except subprocess.TimeoutExpired:
                # Kill the whole tree: grandchildren keep the pipes open and would block communicate().
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
                try:
                    proc.communicate(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
                return ExecuteResponse(
                    output=f"Error: Command timed out after {effective_timeout} seconds.", exit_code=124, truncated=False
                )
            result = subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout)
            if result.stderr:
                output_parts.extend(f"[stderr] {line}" for line in result.stderr.strip().split("\n"))
            output = "\n".join(output_parts) if output_parts else "<no output>"
            truncated = len(output) > self._max_output_bytes
            if truncated:
                output = output[:self._max_output_bytes] + f"\n\n... Output truncated at {self._max_output_bytes} bytes."
            if result.returncode:
                output = f"{output.rstrip()}\n\nExit code: {result.returncode}"
            return ExecuteResponse(output=output, exit_code=result.returncode, truncated=truncated)
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Error: Command timed out after {effective_timeout} seconds.", exit_code=124, truncated=False
            )
        except Exception as exc:  # noqa: BLE001
            return ExecuteResponse(output=f"Error executing command ({type(exc).__name__}): {exc}", exit_code=1, truncated=False)


def make_backend(sandbox: Path):
    """Tạo backend (môi trường thực thi) cho tác tử.

    Yêu cầu:
      - Thư mục gốc (root_dir) là `sandbox`; đường dẫn tương đối `workspace/...` và `skills/...`
        phải dùng được ở CẢ công cụ tệp lẫn shell (shell chạy với thư mục làm việc = `sandbox`).
      - Tác tử chạy được lệnh shell và gọi được `python` (cần đặt PATH).
      - KHÔNG chuyển biến môi trường của bạn vào shell của tác tử (khóa API không được lộ).
    """
    python_dir = str(Path(sys.executable).resolve().parent)
    env = {
        "PATH": f"{python_dir}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(sandbox),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    kwargs = {
        "root_dir": sandbox,
        "virtual_mode": True,
        "inherit_env": False,
        "env": env,
        "timeout": 30,
    }
    # Native Windows uses cmd.exe for LocalShellBackend, while the task instructions
    # and offline tests use POSIX shell commands. Prefer Git Bash when it is available.
    bash_path = shutil.which("bash") if os.name == "nt" else None
    if bash_path:
        return _WindowsBashBackend(**kwargs, bash_path=bash_path)
    return LocalShellBackend(**kwargs)


def build_agent(sandbox: Path, mode: str = "single", use_skills: bool = False, model=None):
    """Tạo tác tử Deep Agents.

    Tham số:
      sandbox:    thư mục chứa `workspace/` (và `skills/` nếu có).
      mode:       "single"    -> tác tử mặc định (có subagent `general-purpose` sẵn của Deep Agents)
                  "subagents" -> thêm các subagent từ `get_subagents()` (nối PATHS_NOTE vào `system_prompt` của MỖI subagent,
                                 vì subagent không nhận BASE_PROMPT) và thêm SUBAGENTS_NOTE vào prompt chính
      use_skills: True -> nạp thư mục "/skills/" qua tham số `skills=` của create_deep_agent
                  và thêm SKILLS_NOTE vào prompt.
      model:      mô hình ngôn ngữ; None -> dùng `make_model()`.
    mode không hợp lệ -> ném ValueError.
    Trả về: đồ thị (graph) đã biên dịch, gọi bằng `.invoke({"messages": [...]})`.
    """
    if mode not in {"single", "subagents"}:
        raise ValueError(f"unknown agent mode: {mode}")

    prompt = BASE_PROMPT
    kwargs = {}
    if mode == "subagents":
        kwargs["subagents"] = [
            {**subagent, "system_prompt": f"{subagent['system_prompt']} {PATHS_NOTE}"}
            for subagent in get_subagents()
        ]
        prompt += SUBAGENTS_NOTE
    if use_skills:
        kwargs["skills"] = ["/skills/"]
        prompt += SKILLS_NOTE

    selected_model = model if model is not None else make_model()
    # OpenRouter exposes reasoning controls in the request body. Keep this optional
    # so the supplied model factory and offline scripted models remain unchanged.
    reasoning_effort = os.getenv("LAB_REASONING_EFFORT") if model is None else None
    if reasoning_effort and hasattr(selected_model, "extra_body"):
        selected_model.extra_body = {"reasoning": {"effort": reasoning_effort}}

    return create_deep_agent(
        model=selected_model,
        system_prompt=prompt,
        backend=make_backend(sandbox),
        **kwargs,
    )

import structlog
import os
from pathlib import Path

from dotenv import load_dotenv

from .utils import ccgram_dir

logger = structlog.get_logger()


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid integer: {exc}") from exc


def _resolve_toolbar_path() -> str:
    env = os.getenv("CCGRAM_TOOLBAR_CONFIG", "").strip()
    if env:
        return env
    fallback = ccgram_dir() / "toolbar.toml"
    return str(fallback) if fallback.exists() else ""


class Config:
    def __init__(self) -> None:
        self.config_dir = ccgram_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)

        for env_path in (Path(".env"), self.config_dir / ".env"):
            if env_path.is_file():
                load_dotenv(env_path)
                logger.debug("Loaded env from %s", env_path.resolve())

        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN") or ""
        if not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

        allowed_users_str = os.getenv("ALLOWED_USERS", "")
        if not allowed_users_str:
            raise ValueError("ALLOWED_USERS environment variable is required")
        try:
            self.allowed_users: set[int] = {
                int(uid.strip()) for uid in allowed_users_str.split(",") if uid.strip()
            }
        except ValueError as e:
            raise ValueError(
                f"ALLOWED_USERS contains non-numeric value: {e}. "
                "Expected comma-separated Telegram user IDs."
            ) from e

        self.tmux_session_name = os.getenv("TMUX_SESSION_NAME", "ccgram")
        self.tmux_main_window_name = "__main__"
        self.own_window_id: str | None = None

        self.state_file = self.config_dir / "state.json"
        self.session_map_file = self.config_dir / "session_map.json"
        self.monitor_state_file = self.config_dir / "monitor_state.json"
        self.events_file = self.config_dir / "events.jsonl"

        _claude_config_dir = os.getenv("CLAUDE_CONFIG_DIR")
        self.claude_config_dir: Path = (
            Path(_claude_config_dir).expanduser()
            if _claude_config_dir
            else Path.home() / ".claude"
        )
        self.claude_projects_path = self.claude_config_dir / "projects"
        self.monitor_poll_interval = max(
            0.5, float(os.getenv("MONITOR_POLL_INTERVAL", "1.0"))
        )
        self.status_poll_interval = max(
            0.5, float(os.getenv("CCGRAM_STATUS_POLL_INTERVAL", "1.0"))
        )

        group_id_str = os.getenv("CCGRAM_GROUP_ID")
        if group_id_str:
            try:
                self.group_id: int | None = int(group_id_str)
            except ValueError as e:
                raise ValueError(f"CCGRAM_GROUP_ID must be a valid integer: {e}") from e
        else:
            self.group_id = None

        self.provider_name: str = os.getenv("CCGRAM_PROVIDER", "claude")

        self.multiplexer_name: str = os.getenv("CCGRAM_MULTIPLEXER", "tmux")

        self.show_hidden_dirs: bool = os.getenv(
            "CCGRAM_SHOW_HIDDEN_DIRS", ""
        ).lower() in ("1", "true", "yes")

        self.ack_reaction: str = os.getenv("CCGRAM_ACK_REACTION", "")

        self._init_forwarding_controls()

        self.whisper_provider: str = os.getenv("CCGRAM_WHISPER_PROVIDER", "")
        self.whisper_api_key: str = os.getenv("CCGRAM_WHISPER_API_KEY", "")
        self.whisper_base_url: str = os.getenv("CCGRAM_WHISPER_BASE_URL", "")
        self.whisper_model: str = os.getenv("CCGRAM_WHISPER_MODEL", "")
        self.whisper_language: str = os.getenv("CCGRAM_WHISPER_LANGUAGE", "")

        self.tts_provider: str = os.getenv("CCGRAM_TTS_PROVIDER", "")
        self.tts_voice: str = os.getenv(
            "CCGRAM_TTS_VOICE", "en-US-EmmaMultilingualNeural"
        )
        self.tts_model: str = os.getenv("CCGRAM_TTS_MODEL", "gpt-4o-mini-tts")
        self.tts_api_key: str = os.getenv("CCGRAM_TTS_API_KEY", "")

        self._init_shell_and_llm()
        self._init_live_view()
        self._init_send()
        self._init_lifecycle()
        self._init_tmux_diagnostics()

        self.hide_tool_calls: bool = os.getenv(
            "CCGRAM_HIDE_TOOL_CALLS", "false"
        ).lower() in ("1", "true", "yes")

        self.ephemeral_tools: bool = os.getenv(
            "CCGRAM_EPHEMERAL_TOOLS", ""
        ).lower() in ("1", "true", "yes")

        raw_status_mode = os.getenv("CCGRAM_STATUS_MODE", "").strip().lower()
        self.status_mode: str = (
            raw_status_mode if raw_status_mode in ("system", "user") else "system"
        )

        logger.debug(
            "Config initialized: dir=%s, allowed_users=%d, tmux_session=%s",
            self.config_dir,
            len(self.allowed_users),
            self.tmux_session_name,
        )

    def _init_forwarding_controls(self) -> None:
        self.forward_tool_flow: bool = os.getenv(
            "CCGRAM_FORWARD_TOOL_FLOW", "1"
        ).lower() in ("1", "true", "yes")
        self.forward_thinking: bool = os.getenv(
            "CCGRAM_FORWARD_THINKING", "1"
        ).lower() in ("1", "true", "yes")
        self.forward_user_echo: bool = os.getenv(
            "CCGRAM_FORWARD_USER_ECHO", "1"
        ).lower() in ("1", "true", "yes")
        self.diagnostic_logs: bool = os.getenv(
            "CCGRAM_DIAGNOSTIC_LOGS", ""
        ).lower() in ("1", "true", "yes")

    def _init_messaging(self) -> None:
        self.msg_auto_spawn: bool = os.getenv("CCGRAM_MSG_AUTO_SPAWN", "").lower() in (
            "1",
            "true",
            "yes",
        )
        self.msg_max_windows: int = _parse_int_env("CCGRAM_MSG_MAX_WINDOWS", 10)
        self.msg_wait_timeout: int = _parse_int_env("CCGRAM_MSG_WAIT_TIMEOUT", 60)
        self.msg_spawn_timeout: int = _parse_int_env("CCGRAM_MSG_SPAWN_TIMEOUT", 300)
        self.msg_spawn_rate: int = _parse_int_env("CCGRAM_MSG_SPAWN_RATE", 3)
        self.msg_rate_limit: int = _parse_int_env("CCGRAM_MSG_RATE_LIMIT", 10)

    def _init_live_view(self) -> None:
        self.live_view_interval: int = max(
            1, _parse_int_env("CCGRAM_LIVE_VIEW_INTERVAL", 5)
        )
        self.live_view_timeout: int = max(
            1, _parse_int_env("CCGRAM_LIVE_VIEW_TIMEOUT", 300)
        )

    def _init_tmux_diagnostics(self) -> None:
        self.tmux_diagnostics: bool = os.getenv(
            "CCGRAM_TMUX_DIAGNOSTICS", ""
        ).lower() in ("1", "true", "yes")
        self.tmux_slow_ms: int = max(0, _parse_int_env("CCGRAM_TMUX_SLOW_MS", 500))

    def _init_shell_and_llm(self) -> None:
        self.prompt_mode = os.getenv("CCGRAM_PROMPT_MODE", "wrap")
        self.prompt_marker = os.getenv("CCGRAM_PROMPT_MARKER", "ccgram")
        self.toolbar_config_path: str = _resolve_toolbar_path()
        self.llm_provider: str = os.getenv("CCGRAM_LLM_PROVIDER", "")
        self.llm_api_key: str = os.getenv("CCGRAM_LLM_API_KEY", "")
        self.llm_base_url: str = os.getenv("CCGRAM_LLM_BASE_URL", "")
        self.llm_model: str = os.getenv("CCGRAM_LLM_MODEL", "")
        try:
            self.llm_temperature: float = float(
                os.getenv("CCGRAM_LLM_TEMPERATURE", "0.1")
            )
        except ValueError as e:
            raise ValueError(
                f"CCGRAM_LLM_TEMPERATURE must be a valid number: {e}"
            ) from e

    def _init_send(self) -> None:
        self.send_search_depth: int = _parse_int_env("CCGRAM_SEND_SEARCH_DEPTH", 5)
        self.send_max_results: int = _parse_int_env("CCGRAM_SEND_MAX_RESULTS", 50)

    def _init_lifecycle(self) -> None:
        self.autoclose_done_minutes: int = int(
            os.getenv("AUTOCLOSE_DONE_MINUTES", "30")
        )
        self.autoclose_dead_minutes: int = int(
            os.getenv("AUTOCLOSE_DEAD_MINUTES", "10")
        )
        self.pane_lifecycle_notify: bool = os.getenv(
            "CCGRAM_PANE_LIFECYCLE_NOTIFY", ""
        ).lower() in ("1", "true", "yes")
        self._init_miniapp()

    def _init_miniapp(self) -> None:
        self.miniapp_base_url: str = os.getenv("CCGRAM_MINIAPP_BASE_URL", "").strip()
        self.miniapp_host: str = os.getenv("CCGRAM_MINIAPP_HOST", "127.0.0.1")
        self.miniapp_port: int = _parse_int_env("CCGRAM_MINIAPP_PORT", 8765)

    def is_user_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_users


config = Config()

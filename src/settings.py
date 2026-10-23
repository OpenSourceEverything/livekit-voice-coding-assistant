"""Small explicit runtime configuration; credentials stay in the environment."""

import math
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    proposals_path: Path
    metrics_path: Path
    usage_path: Path
    llm_model: str
    stt_model: str
    tts_model: str
    tts_voice: str
    adaptive_interruptions: bool
    noise_cancellation: bool
    allow_tests: bool
    allow_proposals: bool
    allow_phone_proposals: bool
    allow_codex: bool
    cloud_demo: bool
    phone_pin: str
    allow_background_jobs: bool = False
    call_log_path: Path = Path("var/calls.jsonl")
    explicit_stop: bool = True
    interruption_min_duration: float = 0.5

    @classmethod
    def from_env(cls) -> "Settings":
        min_duration = float(os.getenv("INTERRUPTION_MIN_DURATION", "0.5"))
        if not math.isfinite(min_duration) or not 0.1 <= min_duration <= 2.0:
            raise ValueError("INTERRUPTION_MIN_DURATION must be 0.1-2.0 seconds.")
        return cls(
            repo_root=Path(os.getenv("REPO_ROOT", "var/demo-repo")),
            proposals_path=Path(os.getenv("PROPOSALS_PATH", "var/proposals")),
            metrics_path=Path(os.getenv("METRICS_PATH", "var/latency.jsonl")),
            usage_path=Path(os.getenv("USAGE_PATH", "var/usage.jsonl")),
            llm_model=os.getenv("LLM_MODEL", "google/gemma-4-31b-it"),
            stt_model=os.getenv("STT_MODEL", "assemblyai/universal-3-6-pro"),
            tts_model=os.getenv("TTS_MODEL", "fishaudio/s2.1-pro"),
            tts_voice=os.getenv("TTS_VOICE", "fa4c9eb3dccc4806b382b40d61c6b10a"),
            # Local VAD avoids depending on the cloud adaptive-classifier quota.
            # Adaptive mode remains an explicit opt-in for controlled comparisons.
            adaptive_interruptions=os.getenv("ADAPTIVE_INTERRUPTION", "false").lower()
            == "true",
            noise_cancellation=os.getenv("NOISE_CANCELLATION", "false").lower()
            == "true",
            allow_tests=os.getenv("ENABLE_LOCAL_TESTS", "false").lower() == "true",
            allow_proposals=os.getenv("ENABLE_PROPOSALS", "true").lower() == "true",
            allow_phone_proposals=os.getenv("ENABLE_PHONE_PROPOSALS", "false").lower()
            == "true",
            allow_codex=os.getenv("ENABLE_CODEX", "false").lower() == "true",
            cloud_demo=os.getenv("COPILOT_MODE", "local") == "cloud-demo",
            phone_pin=os.getenv("PHONE_ACCESS_PIN", ""),
            allow_background_jobs=os.getenv("ENABLE_BACKGROUND_JOBS", "false").lower()
            == "true",
            call_log_path=Path(os.getenv("CALL_LOG_PATH", "var/calls.jsonl")),
            explicit_stop=os.getenv("EXPLICIT_STOP", "true").lower() == "true",
            interruption_min_duration=min_duration,
        )

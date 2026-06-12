"""Pydantic-settings Settings(). Reads `.env`. No network at import time.

Hard requirement at boot: ANTHROPIC_API_KEY + TAVILY_API_KEY. Every other key
defaults to empty so the app boots cleanly with only those two set; the service
each key gates (Pioneer, TrueFoundry, Senso, Composio, Thesys, ClickHouse, x402)
silently no-ops until its dispatch wires it up."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── inference ────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    PREMIUM_MODEL: str = "claude-sonnet-4-6"

    # Cheap tier — defaults to Anthropic OpenAI-compat endpoint with Haiku 4.5
    # (the stand-in we shipped at Receipts on 2026-06-10). D02 swaps these for
    # Pioneer's endpoint + model. CHEAP_API_KEY blank → falls back to
    # ANTHROPIC_API_KEY (so the stand-in boots from one key).
    CHEAP_BASE_URL: str = "https://api.anthropic.com/v1/"
    CHEAP_API_KEY: str = ""
    CHEAP_MODEL: str = "claude-haiku-4-5-20251001"
    CHEAP_INPUT_PER_MTOK: float = 0.05
    CHEAP_OUTPUT_PER_MTOK: float = 0.10
    CHEAP_ATTEMPT_COST_USD: float = 0.0002

    # Pioneer (D02) — adaptive cheap tier + feedback loop. Blank until D02.
    PIONEER_API_KEY: str = ""
    PIONEER_BASE_URL: str = ""
    PIONEER_MODEL: str = ""
    # The feedback / inference-history endpoint Pioneer publishes for adaptive
    # signals. Blank by default — record_feedback() no-ops until set. The
    # dispatch's "don't guess the URL" guard: get the exact path from the
    # Pioneer rep / docs (likely /v1/feedback or /v1/inference-history).
    PIONEER_FEEDBACK_URL: str = ""
    # Pioneer pricing placeholder — same imputed rate as the generic cheap
    # tier. Pioneer hasn't published per-MTok rates we can hard-seed; cost
    # surfaces a non-zero number so the dashboard doesn't silent-zero. Swap
    # for the real rate once the rep / docs page confirms.
    PIONEER_INPUT_PER_MTOK: float = 0.05
    PIONEER_OUTPUT_PER_MTOK: float = 0.10

    # TrueFoundry AI Gateway (D01) — route both tiers through gateway.
    # When TRUEFOUNDRY_BASE_URL + TRUEFOUNDRY_API_KEY are set, clients.py uses
    # OpenAI-SDK-shaped calls against TF for cheap AND premium, with TF_MODEL_*
    # as the model IDs (TF uses "provider-account/model" format from the
    # snippet generator). Blank → fall back to the D00 stand-in pair
    # (Anthropic-compat haiku cheap + Anthropic-native sonnet premium).
    TRUEFOUNDRY_API_KEY: str = ""
    TRUEFOUNDRY_BASE_URL: str = ""
    TF_MODEL_CHEAP: str = ""
    TF_MODEL_PREMIUM: str = ""

    # ── evidence ─────────────────────────────────────────────────────────────
    TAVILY_API_KEY: str = ""
    TAVILY_API_KEY_BACKUP: str = ""

    # ── publish / pay / act / store (all blank until their dispatch) ─────────
    SENSO_API_KEY: str = ""
    X402_PAY_TO: str = ""
    X402_PRICE_USD: float = 0.01
    COMPOSIO_API_KEY: str = ""
    THESYS_C1_API_KEY: str = ""
    CLICKHOUSE_URL: str = ""
    CLICKHOUSE_PASSWORD: str = ""

    # ── sentinel loop (D03) ──────────────────────────────────────────────────
    # WATCH_ENABLED=false disables the autonomous watcher (useful for local
    # dev when you don't want the loop hitting external sites). The demo runs
    # with this on. WATCH_INTERVAL_S is the tick cadence.
    WATCH_ENABLED: bool = True
    WATCH_INTERVAL_S: int = 30
    # Where the live-editable test page lives. Sentinel watches this URL as
    # the is_test vendor. The demo lever — edit a claim here on stage and
    # the loop fires within one interval.
    TEST_VENDOR_NAME: str = "Nimbus Support AI"
    TEST_VENDOR_URL: str = "http://127.0.0.1:8010/test-vendor/nimbus"

    # ── flags ────────────────────────────────────────────────────────────────
    # Magnific honest-ad stage. Magnific is not a sponsor at this hack — OFF.
    # honest_ad.py stays in-tree (with VENDOR_BACKDROP_OVERRIDES) so toggling
    # this flag back on works; orchestrator.py gates the call on this flag.
    HONEST_AD_ENABLED: bool = False
    HONEST_AD_TOP_N: int = 25
    # Kept blank so honest_ad.py imports cleanly when re-enabled by future work.
    # Not surfaced in .env / .env.example because Magnific isn't a sponsor here.
    FREEPIK_API_KEY: str = ""
    HONEST_AD_IMAGE_COMMAND: str = ""

    # ── run knobs (cascade tuning — same as Receipts) ────────────────────────
    N_VENDORS: int = 10
    SEMAPHORE: int = 8
    JUDGE_CONFIDENCE_THRESHOLD: float = 0.7
    SCRAPE_TIMEOUT_S: float = 10.0
    LLM_TIMEOUT_S: float = 45.0
    CHEAP_LLM_TIMEOUT_S: float = 8.0
    # Belt-and-suspenders: route cheap-tier calls to premium when cheap is down.
    CHEAP_FALLBACK_TO_PREMIUM: bool = False


settings = Settings()


def cheap_effective_api_key() -> str:
    """Cheap-tier API key with the Anthropic stand-in fallback. When CHEAP_API_KEY
    is unset, the default cheap base URL is Anthropic OpenAI-compat — reuse the
    ANTHROPIC_API_KEY so a one-key .env still runs the cascade."""
    return settings.CHEAP_API_KEY or settings.ANTHROPIC_API_KEY


def require_boot_keys() -> None:
    """Hard-require only the two keys the engine cannot run without."""
    missing = [
        name
        for name in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY")
        if not getattr(settings, name)
    ]
    if missing:
        raise RuntimeError(
            "Sentinel boot requires " + " + ".join(missing) + " in .env."
        )

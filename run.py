"""启动入口: python run.py"""

import os
from pathlib import Path

import uvicorn
from dotenv import dotenv_values, load_dotenv


def load_env_profile() -> None:
    original_env_keys = set(os.environ)
    load_dotenv()
    profile = os.environ.get("AI_WORK_ENV", "").strip()
    if not profile:
        return

    profile_file = Path(f".env.{profile}")
    if profile_file.exists():
        for key, value in dotenv_values(profile_file).items():
            if value is not None and key not in original_env_keys:
                os.environ[key] = value


if __name__ == "__main__":
    load_env_profile()
    host = os.environ.get("AI_WORK_HOST", "0.0.0.0")
    port = int(os.environ.get("AI_WORK_PORT", "8000"))
    reload = os.environ.get("AI_WORK_RELOAD", "true").lower() in {"1", "true", "yes", "on"}
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)

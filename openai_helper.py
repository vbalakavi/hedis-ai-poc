import os
from pathlib import Path

from openai import OpenAI
import streamlit as st


def iter_env_files(base_dir):
    yield base_dir / ".env"


def load_api_key(base_dir=None):
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        return api_key

    try:
        secret_key = st.secrets.get("OPENAI_API_KEY")
    except Exception:
        secret_key = None
    if secret_key:
        os.environ["OPENAI_API_KEY"] = secret_key
        return secret_key

    if base_dir is None:
        base_dir = Path(__file__).resolve().parent

    seen = set()
    for env_file in iter_env_files(base_dir):
        env_file = env_file.resolve()
        if env_file in seen or not env_file.exists():
            continue
        seen.add(env_file)

        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("OPENAI_API_KEY="):
                _, raw_value = line.split("=", 1)
                cleaned = raw_value.strip().strip("\"'“”")
                if cleaned:
                    os.environ["OPENAI_API_KEY"] = cleaned
                    return cleaned

    return None


def get_client():
    base_dir = Path(__file__).resolve().parent
    api_key = load_api_key(base_dir)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing. Add it to 3_Hedis_AI/.env.")
    return OpenAI(api_key=api_key)

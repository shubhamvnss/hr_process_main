"""
backend.config - environment configuration for the Talent Acquisition Root Cause API.

Nothing is hard-coded: connection settings come from the same PG* variables the rest of the
pipeline uses (db_config.py). Most of this API is read-only over stored output and needs no model
credentials. The one exception is the To-Be State Workflow Designer, which calls a model at
request time - its settings are at the bottom of this file.

    export PGHOST=localhost PGPORT=5432 PGDATABASE=kinetic PGUSER=postgres PGPASSWORD=postgres
    export PGSCHEMA=talent_acquisition
    export KINETIC_ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
    export KINETIC_RC_FALLBACK_FILE=ta_root_causes.json     # optional, see below

Fallback file
-------------
If root_cause_analysis holds no row, the API will load KINETIC_RC_FALLBACK_FILE (the JSON that
root_cause_agent.py writes) and serve it directly, so the endpoint works immediately after an
agent run that could not reach the database. The response marks itself with source = "file".
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)

ALLOWED_ORIGINS = [o.strip() for o in os.getenv(
    "KINETIC_ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]

FALLBACK_FILE = os.getenv("KINETIC_RC_FALLBACK_FILE",
                          os.path.join(PROJECT_ROOT, "ta_root_causes.json"))

MAX_FINDINGS_PAGE = int(os.getenv("KINETIC_RC_MAX_PAGE", "200"))
LOG_LEVEL = os.getenv("KINETIC_LOG_LEVEL", "INFO")


# ------------------------------------------------------------------ model (To-Be designer only)
# The root-cause agent runs on the OpenAI Responses API (gpt-5.1) from the project root. The same
# client is reused here. If Azure OpenAI variables are present they take precedence, so moving to
# Azure is a configuration change rather than a code change:
#
#     # OpenAI (default)
#     export OPENAI_API_KEY=...
#     export KINETIC_TOBE_MODEL=gpt-5.1           # optional
#
#     # Azure OpenAI
#     export AZURE_OPENAI_API_KEY=...
#     export AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
#     export AZURE_OPENAI_API_VERSION=2025-04-01-preview
#     export KINETIC_TOBE_MODEL=<deployment-name>  # on Azure this is the deployment, not the model
MODEL = os.getenv("KINETIC_TOBE_MODEL", "gpt-5.1")
EFFORT = os.getenv("KINETIC_TOBE_EFFORT", "medium")        # none | low | medium | high | default
MAX_OUTPUT_TOKENS = int(os.getenv("KINETIC_TOBE_MAX_TOKENS", "32000"))
REQUEST_TIMEOUT = int(os.getenv("KINETIC_TOBE_TIMEOUT", "600"))


def is_azure() -> bool:
    return bool(os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"))


def model_label() -> str:
    return f"{'azure:' if is_azure() else ''}{MODEL}"


def llm_client():
    """The Responses API client. Imported lazily so the read-only endpoints never need openai."""
    if is_azure():
        from openai import AzureOpenAI
        return AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            timeout=REQUEST_TIMEOUT)
    from openai import OpenAI
    return OpenAI(timeout=REQUEST_TIMEOUT)

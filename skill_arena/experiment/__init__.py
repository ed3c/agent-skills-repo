"""Paired, randomized, replicated Arena experiment contracts."""

from .benchflow_adapter import (
    BenchFlowExperimentAdapter,
    BenchFlowRuntimePolicy,
    enforce_github_models_retirement,
    fetch_github_model_catalog_evidence,
    load_runtime_policy,
    prepare_benchflow_runtime,
    summarize_paired_bundle,
    validate_catalog_evidence,
    validate_preparation,
)
from .model import (
    ExperimentAdapter,
    ExperimentError,
    InvocationCapture,
)
from .provider_policy import (
    LocalProviderPolicy,
    OllamaHttpProbe,
    load_provider_policy,
    load_provider_revocations,
    validate_provider_attempt,
    validate_provider_preflight,
    validate_provider_revocations,
)
from .quote_repair import (
    load_quote_repair_protocol,
    load_quote_repair_task_bundle,
    validate_quote_repair_protocol,
    validate_quote_repair_task_bundle,
)
from .plan import generate_plan, sign_plan, validate_plan, verify_plan_envelope
from .replay import replay_bundle
from .runner import run_experiment

__all__ = [
    "BenchFlowExperimentAdapter",
    "BenchFlowRuntimePolicy",
    "ExperimentAdapter",
    "ExperimentError",
    "InvocationCapture",
    "LocalProviderPolicy",
    "OllamaHttpProbe",
    "enforce_github_models_retirement",
    "fetch_github_model_catalog_evidence",
    "generate_plan",
    "load_provider_policy",
    "load_provider_revocations",
    "load_runtime_policy",
    "load_quote_repair_protocol",
    "load_quote_repair_task_bundle",
    "prepare_benchflow_runtime",
    "validate_provider_attempt",
    "replay_bundle",
    "run_experiment",
    "sign_plan",
    "summarize_paired_bundle",
    "validate_catalog_evidence",
    "validate_plan",
    "validate_provider_preflight",
    "validate_provider_revocations",
    "validate_quote_repair_protocol",
    "validate_quote_repair_task_bundle",
    "validate_preparation",
    "verify_plan_envelope",
]

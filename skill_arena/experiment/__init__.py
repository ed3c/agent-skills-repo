"""Paired, randomized, replicated Arena experiment contracts."""

from .benchflow_adapter import (
    BenchFlowExperimentAdapter,
    BenchFlowRuntimePolicy,
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
from .plan import generate_plan, sign_plan, validate_plan, verify_plan_envelope
from .replay import replay_bundle
from .runner import run_experiment

__all__ = [
    "BenchFlowExperimentAdapter",
    "BenchFlowRuntimePolicy",
    "ExperimentAdapter",
    "ExperimentError",
    "InvocationCapture",
    "fetch_github_model_catalog_evidence",
    "generate_plan",
    "load_runtime_policy",
    "prepare_benchflow_runtime",
    "replay_bundle",
    "run_experiment",
    "sign_plan",
    "summarize_paired_bundle",
    "validate_catalog_evidence",
    "validate_plan",
    "validate_preparation",
    "verify_plan_envelope",
]

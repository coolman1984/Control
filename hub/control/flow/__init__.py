"""The flow engine: durable multi-step automations built from Control's own actions.

A flow is one TOML file. The agent authors it once; it then runs with no model in the loop,
survives a crash mid-run (piece 2 of the master roadmap), and its risky steps run under one
pre-approval instead of asking the owner on every occurrence (see `flow.approve`).
"""
from .model import Flow, Step, FlowError, parse, parse_file, compute_hash, validate
from .engine import run_flow, resume_run

__all__ = ["Flow", "Step", "FlowError", "parse", "parse_file", "compute_hash", "validate",
           "run_flow", "resume_run"]

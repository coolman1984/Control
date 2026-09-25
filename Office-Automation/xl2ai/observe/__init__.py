"""Observation: what happened, in order, with enough context to explain a failure afterwards.

This layer is deliberately separate from rendering. The pipeline announces facts; the console decides how to draw
them and the journal decides how to store them. That split is what makes the same run watchable live, replayable
from a file, and readable by an AI asked to diagnose it.
"""

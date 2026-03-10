"""Milestone C: LangGraph-based multi-turn agent.

This package intentionally keeps the agent logic lightweight:
- Uses LangGraph for orchestration + SQLite checkpointer for persistence.
- Uses the Milestone B NL2SQL pipeline as a "tool".
- Uses simple heuristics for intent + clarification so the scaffold runs without a paid LLM.

You can later replace heuristics with an LLM router and tool-calling.
"""

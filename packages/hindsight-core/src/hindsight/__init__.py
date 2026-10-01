"""Shared code for the hindsight framework and demos."""

from hindsight.llm import get_llm
from hindsight.prompts import init_messages, sys_prompt
from hindsight.tools import get_local_datetime

__all__ = ["get_llm", "init_messages", "sys_prompt", "get_local_datetime"]

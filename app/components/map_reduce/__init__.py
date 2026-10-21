"""Generic, goal-driven map-reduce for content too large for one LLM call (tool outputs, merged evidence).

Self-contained apart from app.components helpers (message text, LLM call naming); no app settings or services.
See summarizer.py for usage.
"""

from app.components.map_reduce.chunking import dataset_head, estimate_tokens, split_into_chunks
from app.components.map_reduce.fidelity import find_unverified_figures
from app.components.map_reduce.summarizer import MapReduceConfig, MapReduceResult, MapReduceSummarizer

__all__ = [
    "MapReduceConfig",
    "MapReduceResult",
    "MapReduceSummarizer",
    "dataset_head",
    "estimate_tokens",
    "find_unverified_figures",
    "split_into_chunks",
]

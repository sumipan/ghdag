"""ghdag — Generic DAG execution engine."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("ghdag")
except PackageNotFoundError:
    __version__ = "unknown"

from ghdag.pipeline.result import QueueTask, QueueTaskStore
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.pipeline.state import PipelineState
from ghdag.dag.engine import DagEngine
from ghdag.workflow.dispatcher import WorkflowDispatcher

__all__ = [
    "QueueTask",
    "QueueTaskStore",
    "LLMPipelineAPI",
    "PipelineState",
    "DagEngine",
    "WorkflowDispatcher",
]

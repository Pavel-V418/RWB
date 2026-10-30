"""Пример structured-агента: читает input_dir/*.md и пишет output_dir/summary.json."""

from agents._example.schemas.summary import DocumentSummary
from specforge.core.agent_api import StructuredAgent
from specforge.core.contracts import AgentSpec


class ExampleAgent(StructuredAgent[DocumentSummary]):
    spec = AgentSpec(
        name="_example",
        stage="example",
        kind="structured",
        description="Краткое изложение markdown-документов (образец для команды)",
        inputs=["*.md"],
        outputs=["summary.json"],
        temperature=0.1,
        token_budget=20_000,
        max_repairs=0,
    )
    schema = DocumentSummary
    output_file = "summary.json"


AGENT = ExampleAgent()

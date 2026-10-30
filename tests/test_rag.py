"""RAG на мини-корпусе fixtures/docs-corpus: индексация, поиск ru/en, фильтр tech, ask_docs."""

from pathlib import Path

import pytest

from specforge.llm import Expect, FakeLLM, FakeReply, FakeToolCall
from specforge.rag.ingest import ingest_corpus
from specforge.rag.lookup_agent import ANSWER_TOKENS, ask_docs
from specforge.rag.store import DocStore
from specforge.tools.docs import SEARCH_TOKEN_LIMIT, search_docs

CORPUS = Path(__file__).parent / "fixtures" / "docs-corpus"


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "docs.db"
    monkeypatch.setenv("SPECFORGE_DOCS_DB", str(path))
    store = DocStore(path)
    assert ingest_corpus(CORPUS, store) >= 6
    assert store.techs() == ["efcore", "org-standards", "postgis"]
    store.close()
    return path


def test_russian_and_english_queries_find_chunk(db: Path) -> None:
    store = DocStore(db)
    ru = store.search("как называть миграции")
    assert (ru[0].tech, ru[0].section) == (
        "org-standards",
        "Стандарт оформления репозитория > Именование миграций",
    )

    en = store.search("create spatial index gist")
    assert en[0].source == "postgis/spatial-index.md"
    assert en[0].section == "Spatial indexing > Creating a GiST index"
    # блок кода целиком в одном чанке
    assert "USING GIST (geom);" in en[0].text
    assert "ANALYZE parking_zones;" in en[0].text

    html = store.search("remove migration", tech="efcore")
    assert html[0].section == "Migrations overview > Remove a migration"
    assert "Docs home" not in " ".join(h.text for h in store.search("docs home search"))


def test_tech_filter_and_search_docs_budget(db: Path) -> None:
    store = DocStore(db)
    both = {h.tech for h in store.search("migrations index")}
    assert {"efcore", "org-standards"} <= both
    only = store.search("migrations index", tech="efcore")
    assert only
    assert {h.tech for h in only} == {"efcore"}

    text = search_docs("index", "postgis", db_path=db)
    assert text.startswith("[1] postgis · ")
    assert "источник: postgis/spatial-index.md" in text
    assert len(text) <= SEARCH_TOKEN_LIMIT * 4
    small = search_docs("index", db_path=db, limit_tokens=80)
    assert small.startswith("[1] ")
    assert len(small) <= 80 * 4 + 1
    assert "Ничего не найдено в kafka" in search_docs("index", "kafka", db_path=db)


def test_ask_docs_on_fake_llm(db: Path) -> None:
    answer = (
        "Создайте GiST-индекс по колонке геометрии.\n```sql\n"
        "CREATE INDEX parking_zones_geom_idx ON parking_zones USING GIST (geom);\n```\n"
        "Источник: postgis/spatial-index.md\n" + "Подробности. " * 200
    )
    llm = FakeLLM(
        [
            FakeReply(
                tool_calls=[
                    FakeToolCall("search_docs", {"query": "spatial index", "tech": "postgis"})
                ],
                expect=Expect(tools=["search_docs", "finish"], thinking=False),
            ),
            FakeReply(
                tool_calls=[FakeToolCall("finish", {"summary": answer})],
                expect=Expect(contains="источник: postgis/spatial-index.md"),
            ),
        ]
    )
    result = ask_docs("Как ускорить пространственные запросы?", "postgis", llm=llm)
    llm.assert_exhausted()
    assert result.startswith("Создайте GiST-индекс")
    assert "Источник: postgis/spatial-index.md" in result
    assert result.count("```") == 2
    assert len(result) <= ANSWER_TOKENS * 4

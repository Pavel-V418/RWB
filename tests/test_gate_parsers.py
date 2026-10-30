import os
import shutil
import time
from pathlib import Path

import pytest

from specforge.core.contracts import Diagnostic
from specforge.gates.parsers import PARSERS, ParseContext, select_parser

FIXTURES = Path(__file__).parent / "fixtures" / "gates"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def parse(parser: str, output: str, workspace: Path, command: str = "cmd") -> list[Diagnostic]:
    ctx = ParseContext(command=command, workspace=workspace, exit_code=1, started_at=time.time())
    return PARSERS[parser](output, ctx)


def brief(diagnostics: list[Diagnostic]) -> list[tuple[object, ...]]:
    return [(d.file, d.line, d.column, d.severity, d.code) for d in diagnostics]


def place_report(workspace: Path, name: str, rel_dir: str) -> Path:
    target = workspace / rel_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / name, target)
    return target


# --- выбор парсера -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "parser"),
    [
        ("dotnet build src/Api -warnaserror", "msbuild"),
        ("dotnet test tests --logger trx", "dotnet-test"),
        ("npx ng build --configuration production", "tsc"),
        ("npx tsc --noEmit -p web", "tsc"),
        ("npx eslint web --format json", "eslint"),
        ("npx ng lint --format json", "eslint"),
        ("./gradlew :app:testDebugUnitTest", "gradle"),
        ("kotlinc src -d out", "kotlinc"),
        ("npx @stoplight/spectral-cli lint api/openapi.yaml -f json", "spectral"),
        ("npx @asyncapi/cli validate api/asyncapi.yaml", "asyncapi"),
        ("psql -v ON_ERROR_STOP=1 -f db/schema.sql", "psql"),
        ("npm test", "generic"),
    ],
)
def test_auto_selection(command: str, parser: str) -> None:
    assert select_parser(command) == parser


def test_explicit_parser_wins_and_unknown_rejected() -> None:
    assert select_parser("make check", {"make check": "tsc"}) == "tsc"
    assert select_parser("npm test", {"other": "tsc"}) == "generic"
    with pytest.raises(ValueError, match="cobol"):
        select_parser("make", {"make": "cobol"})


# --- dotnet --------------------------------------------------------------------------------------


def test_msbuild(tmp_path: Path) -> None:
    found = parse("msbuild", fixture("msbuild_build.txt"), tmp_path)
    assert brief(found)[:4] == [
        ("/work/src/Api/Services/OrderService.cs", 14, 17, "warning", "CS8618"),
        ("/work/src/Api/Program.cs", 12, 5, "error", "CS1002"),
        ("/work/src/Api/Controllers/OrdersController.cs", 27, 20, "error", "CS0103"),
        ("/work/src/Api/Controllers/OrdersController.cs", 31, 13, "error", "CS1503"),
    ]
    assert found[1].message == "; expected"  # без хвоста [проект]
    assert found[-1].code == "CS2001"
    assert found[-1].file is None
    assert found[-1].message.startswith("CSC: Source file")
    assert {d.category for d in found} == {"compile"}


def test_dotnet_test_console(tmp_path: Path) -> None:
    found = parse("trx", fixture("dotnet_test_console.txt"), tmp_path)
    assert brief(found) == [
        ("/work/tests/Api.Tests/OrderServiceTests.cs", 27, None, "error", "test"),
        ("/work/src/Api/Services/OrderService.cs", 21, None, "error", "test"),
    ]
    assert "Total_IsSumOfItems" in found[0].message
    assert "Expected: 30" in found[0].message
    assert "NullReferenceException" in found[1].message
    assert {d.category for d in found} == {"test"}


def test_trx_report_preferred_over_console(tmp_path: Path) -> None:
    place_report(tmp_path, "results.trx", "tests/Api.Tests/TestResults")
    found = parse("dotnet-test", fixture("dotnet_test_console.txt"), tmp_path)
    assert brief(found) == [
        ("/work/tests/Api.Tests/OrderServiceTests.cs", 27, None, "error", "test")
    ]
    assert "Values differ" in found[0].message


def test_stale_reports_ignored(tmp_path: Path) -> None:
    report = place_report(tmp_path, "results.trx", "TestResults")
    old = time.time() - 3600
    os.utime(report, (old, old))
    found = parse("trx", "", tmp_path)
    assert found == []


# --- TypeScript / Angular ------------------------------------------------------------------------


def test_angular_esbuild(tmp_path: Path) -> None:
    found = parse("tsc", fixture("ng_build_esbuild.txt"), tmp_path)
    assert brief(found) == [
        ("src/app/orders/order-list.component.ts", 24, 22, "error", "TS2345"),
        ("src/app/orders/order-form.component.html", 7, 11, "error", "NG8002"),
        ("src/app/app.component.html", 3, 12, "warning", "NG8107"),
    ]
    assert (
        found[0].message
        == "Argument of type 'string' is not assignable to parameter of type 'number'."
    )


def test_tsc_pretty_and_plain(tmp_path: Path) -> None:
    found = parse("tsc", fixture("tsc.txt"), tmp_path)
    assert brief(found) == [
        ("src/app/orders/order.service.ts", 18, 5, "error", "TS2322"),
        ("src/app/orders/order.model.ts", 3, 3, "error", "TS2564"),
        ("src/main.ts", 4, 1, "error", "TS2304"),
    ]


def test_tsc_strips_ansi_and_error_prefix(tmp_path: Path) -> None:
    line = "\x1b[31mError: src/a.ts:1:2 - error TS1005: ';' expected.\x1b[0m"
    assert brief(parse("tsc", line, tmp_path)) == [("src/a.ts", 1, 2, "error", "TS1005")]


def test_eslint_json(tmp_path: Path) -> None:
    found = parse("eslint", fixture("eslint.json"), tmp_path)
    assert brief(found) == [
        (
            "/work/web/src/app/orders/order-list.component.ts",
            3,
            10,
            "error",
            "@typescript-eslint/no-unused-vars",
        ),
        (
            "/work/web/src/app/orders/order-list.component.ts",
            8,
            1,
            "warning",
            "@angular-eslint/prefer-standalone",
        ),
        ("/work/web/src/app/orders/order.service.ts", 22, 17, "error", "eqeqeq"),
    ]
    assert {d.category for d in found} == {"lint"}
    assert parse("eslint", "не json", tmp_path) == []


# --- Kotlin / Gradle -----------------------------------------------------------------------------


def test_kotlinc(tmp_path: Path) -> None:
    found = parse("kotlinc", fixture("gradle_build.txt"), tmp_path)
    base = "file:///work/mobile/app/src/main/java/com/example/app"
    assert brief(found) == [
        (f"{base}/ui/OrderAdapter.kt", 31, 13, "warning", None),
        (f"{base}/data/OrderRepository.kt", 42, 9, "error", None),
        (f"{base}/ui/MainActivity.kt", 18, 30, "error", None),
        ("/work/mobile/app/src/main/java/com/example/app/Legacy.kt", 7, 5, "error", None),
    ]
    assert found[1].message == "Unresolved reference 'fetchOrdersAsync'."


def test_junit_xml_with_source_lookup(tmp_path: Path) -> None:
    source = tmp_path / "mobile/app/src/test/java/com/example/app/OrderRepositoryTest.kt"
    source.parent.mkdir(parents=True)
    source.write_text("class OrderRepositoryTest\n", "utf-8")
    place_report(
        tmp_path,
        "TEST-com.example.app.OrderRepositoryTest.xml",
        "mobile/app/build/test-results/testDebugUnitTest",
    )
    found = parse("gradle", "BUILD FAILED", tmp_path)
    test_file = "mobile/app/src/test/java/com/example/app/OrderRepositoryTest.kt"
    assert brief(found) == [
        (test_file, 27, None, "error", "test"),  # кадры org.junit/opentest4j пропущены
        # OrderRepository.kt:55 нет в workspace — берётся первый кадр с найденным файлом
        (test_file, 40, None, "error", "test"),
    ]
    assert parse("junit", "", tmp_path / "empty") == []
    assert "expected:<30> but was:<10>" in found[0].message
    assert "totalIsSum" in found[0].message
    assert "IllegalStateException" in found[1].message


# --- контракты API, БД, generic ------------------------------------------------------------------


def test_spectral_json(tmp_path: Path) -> None:
    found = parse("spectral", fixture("spectral.json"), tmp_path)
    assert brief(found) == [
        ("/work/api/openapi.yaml", 2, 6, "error", "oas3-schema"),
        ("/work/api/openapi.yaml", 12, 9, "warning", "operation-operationId"),
    ]  # info/hint отброшены
    assert found[1].message.endswith("(paths./orders.get)")


def test_asyncapi_and_spectral_stylish(tmp_path: Path) -> None:
    found = parse("asyncapi", fixture("asyncapi_validate.txt"), tmp_path)
    assert brief(found) == [
        ("api/asyncapi.yaml", 1, 1, "warning", "asyncapi-id"),
        ("api/asyncapi.yaml", 14, 7, "error", "asyncapi-document-resolved"),
        ("api/asyncapi.yaml", 22, 11, "error", "asyncapi-operation-operationId"),
    ]
    assert found[1].message == (
        '"message" property must have required property "payload" '
        "(channels.order/created.messages.created)"
    )
    assert {d.category for d in found} == {"compile"}
    stylish = parse("spectral", fixture("asyncapi_validate.txt"), tmp_path)
    assert [d.category for d in stylish] == ["lint"] * 3


def test_psql(tmp_path: Path) -> None:
    found = parse("psql", fixture("psql_apply.txt"), tmp_path)
    assert brief(found) == [
        ("/work/db/001_schema.sql", 12, None, "error", None),
        ("/work/db/002_items.sql", 5, None, "error", None),
        ("/work/db/003_geo.sql", 9, None, "error", None),
    ]
    assert found[0].message == 'relation "orders" already exists'
    assert "LINE 1: CREATE TABL order_items (" in found[1].message
    assert "CREATE EXTENSION postgis" in found[2].message
    assert parse("psql", 'ERROR:  42P07: relation "x" already exists', tmp_path)[0].code == "42P07"


def test_generic_tail(tmp_path: Path) -> None:
    found = parse("generic", fixture("generic_npm.txt"), tmp_path, command="npm test")
    messages = [d.message for d in found]
    assert "Test Files  1 failed | 1 passed (2)" in messages
    assert "npm error Lifecycle script `test` failed with error:" in messages
    assert all(d.tool == "npm" and d.category == "generic" for d in found)
    assert parse("generic", "Tests: 5 passed, 0 failed\nerrors: 0", tmp_path) == []


def test_every_parser_has_a_fixture_test() -> None:
    tested = {"msbuild", "trx", "dotnet-test", "tsc", "eslint", "kotlinc", "junit", "gradle"}
    tested |= {"spectral", "asyncapi", "psql", "generic"}
    assert tested == set(PARSERS)

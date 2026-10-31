"""Офлайн-тест цикла backend-агента: фейковая LLM, настоящий dotnet build (ключ организаторов не тратится).

    .venv/bin/python -m unittest tests.test_backend_agent
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from specforge_backend.backend.agent import FEATURES, REGISTRATIONS, run, write_registrations
from specforge_backend.backend.llm import Usage

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "reference" / "case1" / "artifacts"

BROKEN = """```csharp
namespace App.Application.Features.Events;
internal sealed class EventsApi : App.Application.Contracts.IEventsApi
{
    public int Broken() => "не число";
}
```"""

FIXED = """Исправил:
```csharp
using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Features.Events;

internal sealed class EventsApi(IAppDbContext db) : IEventsApi
{
    public async Task<DomainEventPageDto> ListEventsAsync(Guid? vehicleId, EventType? type, DateTimeOffset? from, DateTimeOffset? to, int page, int pageSize, CancellationToken cancellationToken)
    {
        var total = await db.DomainEvents.CountAsync(cancellationToken);
        return new DomainEventPageDto { Items = [], Meta = new PageMetaDto { Page = page, PageSize = pageSize, Total = total } };
    }
}
```"""


def fresh_case(artifacts: Path) -> None:
    """Кейс 1 без реализаций агента: остаётся только эталонный AuthApi."""
    shutil.copytree(CASE / "01_requirements", artifacts / "01_requirements")
    shutil.copytree(CASE / "02_system", artifacts / "02_system")
    shutil.copytree(CASE / "04_backend", artifacts / "04_backend",
                    ignore=shutil.ignore_patterns("bin", "obj", "TestResults", ".agent"))
    for feature in (artifacts / "04_backend" / FEATURES).iterdir():
        if feature.name != "Auth":
            shutil.rmtree(feature)
    write_registrations(artifacts / "04_backend")


class FakeLlm:
    def __init__(self, answers: dict[str, list[str]]):
        self.answers = answers
        self.usage = Usage()
        self.prompts: list[str] = []

    def chat(self, system: str, user: str, **_) -> str:
        self.prompts.append(user)
        self.usage.calls += 1
        self.usage.prompt += 1000
        tag = next(t for t in self.answers if f"I{t}Api" in user or f"{t}Api.cs" in user)
        return self.answers[tag].pop(0)


class BackendAgentLoopTest(unittest.TestCase):
    def test_repairs_by_compiler_errors_and_falls_back_to_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            fresh_case(artifacts)
            llm = FakeLlm({"Events": [BROKEN, FIXED], "Notifications": ["нет кода", "нет кода", "нет кода"]})

            result = run(artifacts, artifacts / "04_backend", llm, tags=["Events", "Notifications"], log=lambda *_: None, tests=False)

            backend = artifacts / "04_backend"
            self.assertEqual({"Events": "implemented", "Notifications": "stub"}, result.tags)
            self.assertFalse(result.passed)  # заглушка = шлюз не пройден, но сборка зелёная
            self.assertEqual(5, result.attempts)
            self.assertRegex(llm.prompts[1], r"EventsApi\.cs:\d+: CS\d{4}")  # ремонт получил ошибку компилятора
            self.assertIn("FeatureNotImplementedException", (backend / FEATURES / "Notifications" / "NotificationsApi.cs").read_text())
            registrations = (backend / REGISTRATIONS).read_text()
            for iface in ("IAuthApi", "IEventsApi", "INotificationsApi"):
                self.assertIn(iface, registrations)
            report = json.loads((backend / "backend-agent.json").read_text())
            self.assertFalse([e for e in report["errors"] if "Notifications" not in json.dumps(e, ensure_ascii=False)])


if __name__ == "__main__":
    unittest.main()


class SecurityPolicyTest(unittest.TestCase):
    def test_raw_sql_is_rejected_before_build_and_sent_to_repair(self):
        raw = FIXED.replace("var total = await db.DomainEvents.CountAsync(cancellationToken);",
                            'var total = await db.DomainEvents.FromSqlRaw("SELECT * FROM domain_events WHERE x = \'" + type + "\'").CountAsync(cancellationToken);')
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            fresh_case(artifacts)
            llm = FakeLlm({"Events": [raw, FIXED]})

            result = run(artifacts, artifacts / "04_backend", llm, tags=["Events"], log=lambda *_: None, tests=False)

            self.assertEqual({"Events": "implemented"}, result.tags)
            self.assertIn("SEC001: FromSqlRaw", llm.prompts[1])
            trace = artifacts / "04_backend" / ".agent"
            self.assertIn("SEC001", (trace / "Events-1.errors.txt").read_text())
            self.assertEqual("OK\n", (trace / "Events-2.errors.txt").read_text())


class FeedbackRepairTest(unittest.TestCase):
    def test_runtime_feedback_repairs_from_current_code_and_keeps_it_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            fresh_case(artifacts)
            backend = artifacts / "04_backend"
            run(artifacts, backend, FakeLlm({"Events": [FIXED]}), tags=["Events"], log=lambda *_: None, tests=False)
            working = (backend / FEATURES / "Events" / "EventsApi.cs").read_text()

            ok_llm = FakeLlm({"Events": [FIXED.replace("Items = []", "Items = [] /* fixed */")]})
            ok = run(artifacts, backend, ok_llm, tags=["Events"], log=lambda *_: None, feedback="500: FK violation", tests=False)
            bad = run(artifacts, backend, FakeLlm({"Events": [BROKEN, BROKEN]}), tags=["Events"], max_attempts=2,
                      log=lambda *_: None, feedback="500: FK violation", tests=False)

            self.assertEqual({"Events": "repaired"}, ok.tags)
            self.assertIn("FK violation", ok_llm.prompts[0])
            self.assertIn("ListEventsAsync", ok_llm.prompts[0])  # текущий код ушёл в промпт
            self.assertEqual({"Events": "unchanged"}, bad.tags)
            self.assertIn("/* fixed */", (backend / FEATURES / "Events" / "EventsApi.cs").read_text())
            self.assertNotEqual(working, (backend / FEATURES / "Events" / "EventsApi.cs").read_text())


JOB = """```csharp
using App.Application.Abstractions;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Features.Jobs;

internal sealed class SessionExpiryJob(IAppDbContext db, TimeProvider time) : IScheduledJob
{
    public string Name => "session-expiry";

    public TimeSpan Interval => TimeSpan.FromMinutes(1);

    public async Task<int> RunAsync(CancellationToken cancellationToken)
    {
        var now = time.GetUtcNow();
        var expired = await db.ParkingSessions.Where(s => s.Status == "active" && s.PaidUntil <= now).Take(100).ToListAsync(cancellationToken);
        foreach (var s in expired)
        {
            s.Status = "expired";
        }

        await db.SaveChangesAsync(cancellationToken);
        return expired.Count;
    }
}
```"""


class JobsStepTest(unittest.TestCase):
    def test_jobs_are_generated_built_and_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            fresh_case(artifacts)
            llm = FakeLlm({"Jobs": [JOB]})
            llm.answers = {"Jobs": [JOB]}
            llm.chat = lambda system, user, **_: (llm.prompts.append(user), llm.answers["Jobs"].pop(0))[1]

            result = run(artifacts, artifacts / "04_backend", llm, tags=["Jobs"], log=lambda *_: None, tests=False)

            self.assertEqual({"Jobs": "implemented"}, result.tags)
            self.assertIn("FR-9", llm.prompts[0])  # требование про 10 минут попало в контекст
            registrations = (artifacts / "04_backend" / REGISTRATIONS).read_text()
            self.assertIn("services.AddScoped<IScheduledJob, App.Application.Features.Jobs.SessionExpiryJob>();", registrations)

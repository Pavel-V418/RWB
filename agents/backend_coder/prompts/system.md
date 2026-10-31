# backend_coder

Промпты этого агента живут в части участника 3: `packages/specforge-backend/specforge_backend/backend/prompts.py`
(+ эталоны `example_api.cs`, `example_tests.cs`). Агент платформы — адаптер (`agent.py`): вызовы модели идут
через LLMClient, изменения пакета сбрасывают кэш этапа.

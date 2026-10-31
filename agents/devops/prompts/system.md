# devops

Промпт этого агента живёт в части участника 3: `packages/specforge-backend/specforge_backend/devops/agent.py`
(SYSTEM). Агент платформы — адаптер (`agent.py`): вызовы модели идут через LLMClient, изменения пакета
сбрасывают кэш этапа.

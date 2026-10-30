# ux_designer

Промпты этого агента живут в части участника 4: `packages/specforge-web/specforge_web/agents/` (prompts.py, agent.py).
Агент платформы — адаптер (`agent.py`): вызовы модели идут через LLMClient, изменения пакета сбрасывают кэш этапа.

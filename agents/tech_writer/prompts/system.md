# tech_writer

Промпты этого агента живут в части участника 2: `packages/specforge-analysis/specforge_analysis/prompts/*.j2`.
Агент платформы — адаптер (`agent.py`): вызовы модели идут через LLMClient, изменения пакета сбрасывают кэш этапа.

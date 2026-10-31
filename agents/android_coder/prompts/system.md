# android_coder

Промпты этого агента живут в части участника 5: `packages/specforge-mobile/specforge_mobile/android_agent/prompts.py`
(правила каркаса — `packages/specforge-mobile/android/AGENT_CONTRACT.md`). Агент платформы — адаптер (`agent.py`):
вызовы модели идут через LLMClient, изменения пакета сбрасывают кэш этапа.

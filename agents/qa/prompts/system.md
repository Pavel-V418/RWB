# qa

Промпт LLM-триажа находок сканеров живёт в части участника 5: `packages/specforge-mobile/specforge_mobile/tools/security/triage.py`
(PROMPT). Остальное — без LLM: контрактные проверки из OpenAPI, e2e по сценарию, отчёты. Агент платформы — адаптер
(`agent.py`): вызовы модели идут через LLMClient, изменения пакета сбрасывают кэш этапа.

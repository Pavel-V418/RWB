-- Выполняется один раз при создании тома postgres.
-- База платформы (POSTGRES_DB): pgvector для RAG, чекпоинты LangGraph.
CREATE EXTENSION IF NOT EXISTS vector;
-- pg_trgm — поиск похожих ошибок в памяти исправлений (specforge/repair/fix_memory.py).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Отдельная база для Langfuse (её схемой управляют миграции Langfuse).
CREATE DATABASE langfuse;

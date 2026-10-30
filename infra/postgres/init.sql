-- Выполняется один раз при создании тома postgres.
-- База платформы (POSTGRES_DB): pgvector для RAG, чекпоинты LangGraph.
CREATE EXTENSION IF NOT EXISTS vector;

-- Отдельная база для Langfuse (её схемой управляют миграции Langfuse).
CREATE DATABASE langfuse;

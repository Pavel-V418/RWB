from pydantic import BaseModel, Field


class DocumentSummary(BaseModel):
    title: str = Field(description="Короткий заголовок документа")
    summary: str = Field(description="Суть документа в 2–4 предложениях")
    key_points: list[str] = Field(description="Ключевые тезисы, по одному на элемент")
    open_questions: list[str] = Field(
        description="Неясности и вопросы к автору документа; пустой список, если их нет"
    )

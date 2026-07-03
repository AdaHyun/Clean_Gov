from .models import ArticleRecordModel


def pydantic_validate(record: dict):
    return ArticleRecordModel.model_validate(record)

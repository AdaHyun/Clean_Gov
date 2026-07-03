from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    site_name: str = ""
    site_domain: str = ""
    site_url: str = ""
    channel_name: str = ""
    channel_url: str = ""


class OrganizationModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    source_department: str = ""
    issuing_authority: list[str] = Field(default_factory=list)
    joint_departments: list[str] = Field(default_factory=list)


class ClassificationModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    policy_level: str = ""
    document_type: str = ""
    policy_category: str = ""
    topic_tags: list[str] = Field(default_factory=list)
    target_region: str = ""


class DatesModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    publish_date: str = ""
    crawl_date: str = ""


class ContentModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    body_text: str = ""
    body_html: str = ""
    summary: str = ""


class CrawlModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    crawler_name: str = ""
    crawl_status: str = ""
    http_status: int | None = None
    raw_html_path: str = ""


class RawModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    raw_title: str = ""
    raw_date: str = ""
    raw_source: str = ""


class ArticleRecordModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    doc_id: str = ""
    title: str = ""
    url: str = ""
    source: SourceModel = Field(default_factory=SourceModel)
    organization: OrganizationModel = Field(default_factory=OrganizationModel)
    classification: ClassificationModel = Field(default_factory=ClassificationModel)
    dates: DatesModel = Field(default_factory=DatesModel)
    content: ContentModel = Field(default_factory=ContentModel)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    images: list[dict[str, Any]] = Field(default_factory=list)
    crawl: CrawlModel = Field(default_factory=CrawlModel)
    raw: RawModel = Field(default_factory=RawModel)

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Article:
    id: Optional[int]
    title: str
    url: str
    summary: str
    published_at: Optional[datetime]


@dataclass
class Feedback:
    id: Optional[int]
    article_id: int
    is_like: bool
    created_at: Optional[datetime]

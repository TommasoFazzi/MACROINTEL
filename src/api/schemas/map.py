"""Pydantic schemas for Intelligence Map API responses."""
from typing import Optional
from pydantic import BaseModel


class EntityProperties(BaseModel):
    """GeoJSON Feature properties for a map entity."""
    id: int
    name: str
    entity_type: str
    mention_count: int
    metadata: dict = {}


class EntityGeometry(BaseModel):
    """GeoJSON Point geometry."""
    type: str = "Point"
    coordinates: list[float]  # [lng, lat]


class EntityFeature(BaseModel):
    """GeoJSON Feature for a single entity."""
    type: str = "Feature"
    geometry: EntityGeometry
    properties: EntityProperties


class EntityCollection(BaseModel):
    """GeoJSON FeatureCollection of entities."""
    type: str = "FeatureCollection"
    features: list[EntityFeature]
    total_count: int = 0
    filtered_count: int = 0


class EntityArticle(BaseModel):
    """An article related to an entity."""
    id: int
    title: str
    link: Optional[str] = None
    published_date: Optional[str] = None
    source: Optional[str] = None


class EntityStoryline(BaseModel):
    """A storyline linked to an entity (via entity_mentions → articles → article_storylines)."""
    id: int
    title: str
    narrative_status: str
    momentum_score: float
    article_count: int
    community_id: Optional[int] = None


class EntityDetail(BaseModel):
    """Full entity detail for the dossier panel."""
    id: int
    name: str
    entity_type: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    mention_count: int
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    metadata: dict = {}
    related_articles: list[EntityArticle] = []
    related_storylines: list[EntityStoryline] = []


class MapStats(BaseModel):
    """Live stats for the HUD overlay."""
    total_entities: int
    geocoded_entities: int
    active_storylines: int
    entity_types: dict[str, int] = {}

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Entity

router = APIRouter()

class EntityCreate(BaseModel):
    name: str
    type: str
    synonyms: List[str] = []
    references: List[str] = []

class EntityResponse(BaseModel):
    id: int
    name: str
    type: str
    synonyms: List[str]
    references: List[str]

    class Config:
        from_attributes = True

@router.get("/", response_model=List[EntityResponse])
async def list_entities(db: Session = Depends(get_db)):
    """List all entities"""
    entities = db.query(Entity).all()
    return entities

@router.post("/", response_model=EntityResponse)
async def create_entity(entity: EntityCreate, db: Session = Depends(get_db)):
    """Create a new entity"""
    db_entity = Entity(
        name=entity.name,
        type=entity.type,
        synonyms=entity.synonyms,
        references=entity.references
    )
    db.add(db_entity)
    db.commit()
    db.refresh(db_entity)
    return db_entity

@router.delete("/{entity_id}")
async def delete_entity(entity_id: int, db: Session = Depends(get_db)):
    """Delete an entity"""
    entity = db.query(Entity).filter(Entity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    db.delete(entity)
    db.commit()
    return {"message": "Entity deleted successfully"}
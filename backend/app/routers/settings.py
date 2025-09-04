from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict, Any
from ..database import get_db
from ..models import Settings as SettingsModel
import json

router = APIRouter()

class SettingsUpdate(BaseModel):
    openai_api_key: str
    anthropic_api_key: str
    default_model: str
    max_tokens: int
    temperature: float
    database_url: str
    debug_mode: bool

@router.get("/")
async def get_settings(db: Session = Depends(get_db)):
    """Get all settings"""
    settings = db.query(SettingsModel).all()
    settings_dict = {}
    
    for setting in settings:
        try:
            # Try to parse as JSON first (for complex values)
            settings_dict[setting.key] = json.loads(setting.value)
        except:
            # Otherwise use as string
            settings_dict[setting.key] = setting.value
    
    # Provide defaults if not set
    defaults = {
        "openai_api_key": "",
        "anthropic_api_key": "",
        "default_model": "gpt-4",
        "max_tokens": 2048,
        "temperature": 0.7,
        "database_url": "",
        "debug_mode": False
    }
    
    for key, value in defaults.items():
        if key not in settings_dict:
            settings_dict[key] = value
    
    return settings_dict

@router.put("/")
async def update_settings(settings: SettingsUpdate, db: Session = Depends(get_db)):
    """Update settings"""
    try:
        settings_data = settings.dict()
        
        for key, value in settings_data.items():
            # Convert non-string values to JSON
            if not isinstance(value, str):
                value = json.dumps(value)
            
            # Check if setting exists
            existing = db.query(SettingsModel).filter(SettingsModel.key == key).first()
            
            if existing:
                existing.value = value
            else:
                new_setting = SettingsModel(key=key, value=value)
                db.add(new_setting)
        
        db.commit()
        return {"message": "Settings updated successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
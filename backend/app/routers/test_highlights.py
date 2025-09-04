from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Store for testing - in production this would come from the PDF viewer
test_pdf_text = None

class TextItem(BaseModel):
    str: str
    x: float
    y: float
    width: float
    height: float

class TestHighlightRequest(BaseModel):
    search_term: str
    text_items: List[TextItem]
    case_sensitive: bool = False

class HighlightMatch(BaseModel):
    text: str
    item_index: int
    char_index: int
    x: float
    y: float
    width: float
    height: float
    full_item_text: str

class TestHighlightResponse(BaseModel):
    search_term: str
    total_items: int
    matches: List[HighlightMatch]
    sample_texts: List[str]  # First 5 text items for debugging

@router.post("/test-highlight")
async def test_highlight_search(request: TestHighlightRequest):
    """Test endpoint for debugging PDF text highlighting"""
    
    logger.info(f"Testing highlight for term: '{request.search_term}'")
    logger.info(f"Total text items: {len(request.text_items)}")
    
    matches = []
    sample_texts = []
    
    # Get sample texts for debugging
    for i, item in enumerate(request.text_items[:5]):
        sample_texts.append(f"Item {i}: '{item.str}'")
    
    # Search for matches
    search_term = request.search_term if request.case_sensitive else request.search_term.lower()
    
    for item_index, item in enumerate(request.text_items):
        item_text = item.str if request.case_sensitive else item.str.lower()
        
        # Find all occurrences in this text item
        search_index = 0
        while (search_index := item_text.find(search_term, search_index)) != -1:
            # Calculate approximate position for the match
            # This is simplified - real implementation would need font metrics
            char_width = item.width / len(item.str) if item.str else 0
            match_x = item.x + (char_width * search_index)
            match_width = char_width * len(search_term)
            
            match = HighlightMatch(
                text=item.str[search_index:search_index + len(search_term)],
                item_index=item_index,
                char_index=search_index,
                x=match_x,
                y=item.y,
                width=match_width,
                height=item.height,
                full_item_text=item.str
            )
            matches.append(match)
            
            logger.info(f"Found match in item {item_index}: '{match.text}' at char index {search_index}")
            
            search_index += len(search_term)
    
    logger.info(f"Total matches found: {len(matches)}")
    
    return TestHighlightResponse(
        search_term=request.search_term,
        total_items=len(request.text_items),
        matches=matches,
        sample_texts=sample_texts
    )

@router.get("/test-data")
async def get_test_data():
    """Get sample PDF text data for testing"""
    
    # Sample text items that simulate PDF.js output
    sample_items = [
        TextItem(str="The", x=50, y=100, width=20, height=12),
        TextItem(str="gene", x=75, y=100, width=30, height=12),
        TextItem(str="expression", x=110, y=100, width=60, height=12),
        TextItem(str="of", x=175, y=100, width=15, height=12),
        TextItem(str="Drosophila", x=195, y=100, width=55, height=12),
        TextItem(str="genes", x=255, y=100, width=35, height=12),
        TextItem(str="is", x=295, y=100, width=10, height=12),
        TextItem(str="regulated", x=310, y=100, width=50, height=12),
        TextItem(str="by", x=365, y=100, width=15, height=12),
        TextItem(str="genetic", x=385, y=100, width=40, height=12),
        TextItem(str="factors.", x=430, y=100, width=45, height=12),
        TextItem(str="Gene", x=50, y=120, width=30, height=12),
        TextItem(str="mutations", x=85, y=120, width=55, height=12),
        TextItem(str="can", x=145, y=120, width=20, height=12),
        TextItem(str="affect", x=170, y=120, width=35, height=12),
        TextItem(str="protein", x=210, y=120, width=40, height=12),
        TextItem(str="function.", x=255, y=120, width=50, height=12),
    ]
    
    return {
        "message": "Sample text items for testing highlighting",
        "items": sample_items,
        "test_terms": ["gene", "protein", "Drosophila"],
        "usage": "POST these items to /api/test-highlight with a search_term"
    }
from fastapi import APIRouter
from pathlib import Path
import json
from datetime import datetime, timezone

router = APIRouter()


def _find_config_path() -> Path:
    search_start = Path(__file__).resolve().parent
    for candidate in [search_start, *search_start.parents]:
        p = candidate / "resources" / "store_layout.json"
        if p.exists():
            return p
    for fallback in [search_start / "resources", Path("/app/resources"), Path("/app/data")]:
        p = fallback / "store_layout.json"
        if p.exists():
            return p
    return search_start.parent / "resources" / "store_layout.json"


CONFIG_PATH = _find_config_path()
_DEBUG_PATH = str(CONFIG_PATH)


@router.get("/config")
async def get_store_config():
    if not CONFIG_PATH.exists():
        return {"stores": []}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["_fetched_at"] = datetime.now(timezone.utc).isoformat()
    return data

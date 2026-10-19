import time
import json
import asyncio
from typing import Optional, Any
from utils.logger import get_logger
from config import settings

logger = get_logger(__name__)

# Attempt to load redis for production environments
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

class MemoryCache:
    """Thread-safe, in-memory cache manager replicating standard Redis operations."""
    def __init__(self):
        self._cache = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            if key not in self._cache:
                return None
            val, expire_time = self._cache[key]
            if expire_time is not None and time.time() > expire_time:
                del self._cache[key]
                return None
            return val

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        async with self._lock:
            expire_time = (time.time() + ex) if ex is not None else None
            self._cache[key] = (value, expire_time)
            return True

    async def delete(self, key: str) -> bool:
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

class CacheManager:
    """Dual-mode cache framework. Automatically falls back to in-memory cache if Redis is down."""
    def __init__(self):
        self._redis_client = None
        self._memory_cache = MemoryCache()
        self._connected = False

    async def _init_redis(self):
        if not REDIS_AVAILABLE:
            return
        try:
            self._redis_client = aioredis.from_url(
                settings.redis_url, 
                socket_timeout=1.0, 
                socket_connect_timeout=1.0,
                decode_responses=True
            )
            await self._redis_client.ping()
            self._connected = True
            logger.info(f"CacheManager: Connected to Redis successfully at {settings.redis_url}")
        except Exception:
            self._connected = False
            self._redis_client = None

    async def get(self, key: str) -> Optional[Any]:
        if not self._connected:
            await self._init_redis()
            
        if self._connected and self._redis_client:
            try:
                val = await self._redis_client.get(key)
                if val:
                    return json.loads(val)
            except Exception:
                self._connected = False
        
        # Memory Cache Fallback
        val = await self._memory_cache.get(key)
        return json.loads(val) if val else None

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        if not self._connected:
            await self._init_redis()
            
        val_str = json.dumps(value)
        if self._connected and self._redis_client:
            try:
                await self._redis_client.set(key, val_str, ex=ex)
                return True
            except Exception:
                self._connected = False

        # Memory Cache Fallback
        await self._memory_cache.set(key, val_str, ex=ex)
        return True

    async def delete(self, key: str) -> bool:
        if not self._connected:
            await self._init_redis()
            
        if self._connected and self._redis_client:
            try:
                await self._redis_client.delete(key)
                return True
            except Exception:
                self._connected = False

        # Memory Cache Fallback
        await self._memory_cache.delete(key)
        return True

cache = CacheManager()

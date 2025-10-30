"""
Base classes for lazy computation graph.

LazyNode: Abstract base for typed, cached computation nodes.
"""
from typing import Generic, TypeVar, Any
from abc import ABC, abstractmethod

T = TypeVar('T')  # Output type of a node


class LazyNode(ABC, Generic[T]):
    """
    Base class for lazy computation nodes.
    
    Each subclass:
    1. Declares output type via Generic[T]
    2. Implements compute() method with typed return
    3. Declares dependencies in __init__ with proper types
    
    Caching:
    - Results are cached based on parameters
    - Same parameters = same cached result
    - Different parameters = recomputation
    
    Example:
        class SeasonNode(LazyNode[Season]):
            def compute(self, next_gameweek: int) -> Season:
                season = Season()
                for gw in range(1, next_gameweek):
                    season.play(Query.fixtures_by_gameweek(gw))
                return season
        
        season_node = SeasonNode()
        season = season_node(next_gameweek=6)  # Computes
        season2 = season_node(next_gameweek=6)  # Cache hit
    """
    
    def __init__(self):
        self._cache: dict[tuple[tuple[str, Any], ...], T] = {}
    
    @abstractmethod
    def compute(self, **params) -> T:
        """
        Compute this node's value.
        
        Override in subclasses to implement computation logic.
        Dependencies are called within this method with appropriate params.
        
        Args:
            **params: Named parameters for computation
            
        Returns:
            Computed value of type T
        """
        raise NotImplementedError
    
    def __call__(self, **params) -> T:
        """
        Execute with caching.

        Converts params to cache key and returns cached result if available,
        otherwise computes and caches.
        """
        # Convert params to hashable cache key
        cache_items = []
        for k, v in sorted(params.items()):
            # Convert lists to tuples for hashing
            if isinstance(v, list):
                v = tuple(v)
            cache_items.append((k, v))
        cache_key = tuple(cache_items)

        if cache_key not in self._cache:
            self._cache[cache_key] = self.compute(**params)
        return self._cache[cache_key]
    
    def clear_cache(self):
        """Clear all cached results for this node."""
        self._cache.clear()
    
    @property
    def cache_size(self) -> int:
        """Number of cached results."""
        return len(self._cache)


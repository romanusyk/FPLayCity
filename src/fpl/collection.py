"""
Generic indexed collection system for fast lookups by any field combination.

Purpose: Provides in-memory database-like functionality with multiple indices for efficient queries.
Instead of linear searches, items are indexed by specified fields for O(1) lookups.

Example usage:
    # Create a collection of teams indexed by team_id
    Teams = Collection[Team]([SimpleIndex('team_id')])
    Teams.add(Team(team_id=1, name="Arsenal"))
    arsenal = Teams.get_one(team_id=1)  # Fast O(1) lookup
    
    # Collection with multiple indices
    Fixtures = Collection[Fixture](
        simple_indices=[SimpleIndex('fixture_id')],      # Unique lookup
        list_indices=[ListIndex('gameweek')],            # Get all fixtures in a gameweek
    )
    
Components:
- BaseIndex: Abstract base for any index type
- SimpleIndex: One-to-one mapping (like a primary key or unique index)
- ListIndex: One-to-many mapping (like a non-unique database index)
- IndexGroup: Container managing multiple indices of the same type
- Collection: Main class combining items storage with fast indexed access
"""
from typing import Generic, TypeVar


Item = TypeVar('Item')


class BaseIndex(Generic[Item]):
    """
    Abstract base class for indexing items by one or more fields.
    
    Extracts key values from items and stores them in a mapping structure.
    Subclasses define whether the index is unique (SimpleIndex) or allows duplicates (ListIndex).
    
    Attributes:
        key_fields: Tuple of field names to index by, sorted for consistent lookup
    """

    key_fields: tuple[str, ...]

    def __init__(self, *key_fields: str):
        """Initialize index with specified field names."""
        self.key_fields = tuple(sorted(key_fields))

    def key_value(self, item: Item) -> tuple:
        """Extract the key value tuple from an item based on key_fields."""
        return tuple(item.__getattribute__(key_field) for key_field in self.key_fields)

    def add(self, item: Item) -> None:
        """Add an item to the index. Must be implemented by subclasses."""
        raise NotImplemented

    def get(self, **keys):
        """Retrieve item(s) by key values. Must be implemented by subclasses."""
        raise NotImplemented


class SimpleIndex(BaseIndex[Item]):
    """
    Unique index mapping key values to single items (one-to-one).
    
    Like a primary key or unique constraint in a database - each key value maps to exactly one item.
    By default, raises an assertion error if duplicate keys are added.
    
    Example:
        index = SimpleIndex('player_id')
        index.add(Player(player_id=1, name="Salah"))
        player = index.get(player_id=1)  # Returns the Salah player object
    
    Attributes:
        _map: Internal dictionary mapping key tuples to items
        allow_overwrite: If True, allows replacing existing items with same key
    """

    _map: dict[tuple, Item]
    allow_overwrite: bool = False

    def __init__(self, *key_fields: str, allow_overwrite: bool = False):
        """Initialize unique index with specified field names."""
        super().__init__(*key_fields)
        self._map = {}
        self.allow_overwrite = allow_overwrite

    def add(self, item: Item) -> None:
        """Add item to index. Asserts key uniqueness unless allow_overwrite=True."""
        key_value = self.key_value(item)
        if not self.allow_overwrite:
            assert key_value not in self._map
        self._map[key_value] = item

    def get(self, **keys) -> Item:
        """Retrieve the single item matching the key values."""
        key_values = tuple(keys[field] for field in self.key_fields)
        return self._map[key_values]


class ListIndex(BaseIndex[Item]):
    """
    Non-unique index mapping key values to lists of items (one-to-many).
    
    Like a non-unique database index - allows multiple items to share the same key value.
    Useful for grouping items by a common attribute (e.g., all fixtures in a gameweek).
    
    Example:
        index = ListIndex('gameweek')
        index.add(Fixture(fixture_id=1, gameweek=5, ...))
        index.add(Fixture(fixture_id=2, gameweek=5, ...))
        fixtures = index.get(gameweek=5)  # Returns list of both fixtures
    
    Attributes:
        _map: Internal dictionary mapping key tuples to lists of items
    """

    _map: dict[tuple, list[Item]]

    def __init__(self, *key_fields: str):
        """Initialize non-unique index with specified field names."""
        super().__init__(*key_fields)
        self._map = {}

    def add(self, item: Item) -> None:
        """Add item to the list for this key value (creates new list if needed)."""
        key_value = self.key_value(item)
        if key_value not in self._map:
            self._map[key_value] = []
        self._map[key_value].append(item)

    def get(self, **keys) -> list[Item]:
        """Retrieve the list of all items matching the key values."""
        key_values = tuple(keys[field] for field in self.key_fields)
        return self._map[key_values]


class IndexGroup(Generic[Item]):
    """
    Container managing multiple indices of the same type (all SimpleIndex or all ListIndex).
    
    Automatically routes add() calls to all contained indices and resolves which index
    to use for a given query based on the provided key fields.
    
    Example:
        # Group with indices on different fields
        group = IndexGroup(
            SimpleIndex('player_id'),
            SimpleIndex('player_id', 'fixture_id'),  # Composite index
        )
        group.add(item)  # Adds to both indices
        index = group.resolve_index(player_id=1)  # Returns first index
    
    Attributes:
        indices: Dict mapping key field tuples to their corresponding index objects
    """

    indices: dict[tuple[str, ...], BaseIndex[Item]]

    def __init__(self, *indices):
        """Initialize with multiple index objects."""
        self.indices = {}
        for index in indices:
            assert index.key_fields not in self.indices
            self.indices[index.key_fields] = index

    def add(self, item: Item) -> None:
        """Add item to all indices in this group."""
        for index in self.indices.values():
            index.add(item)

    def resolve_index(self, **keys) -> BaseIndex[Item]:
        """Find the index that matches the provided key field names."""
        key_names = keys.keys()
        return self.indices[tuple(sorted(key_names))]


class Collection(Generic[Item]):
    """
    Main indexed collection class combining storage with fast multi-index lookups.
    
    Stores items in a list while maintaining multiple indices for efficient queries.
    Separates unique lookups (simple_indices) from multi-item queries (list_indices).
    
    Example:
        # Create collection with unique and non-unique indices
        Fixtures = Collection[Fixture](
            simple_indices=[SimpleIndex('fixture_id')],     # Get one fixture by ID
            list_indices=[ListIndex('gameweek')],           # Get all fixtures in gameweek
        )
        
        # Add items (automatically indexed)
        Fixtures.add(fixture1)
        Fixtures.add(fixture2)
        
        # Fast lookups
        fixture = Fixtures.get_one(fixture_id=42)           # Returns single fixture
        gw_fixtures = Fixtures.get_list(gameweek=5)         # Returns list of fixtures
        all_fixtures = Fixtures.items                        # Direct access to all items
    
    Attributes:
        items: List of all items in insertion order
        simple_indices: Group of unique indices for get_one() queries
        list_indices: Group of non-unique indices for get_list() queries
    """

    items: list[Item]
    simple_indices: IndexGroup[Item]
    list_indices: IndexGroup[Item]

    def __init__(
            self,
            simple_indices: list[SimpleIndex[Item]],
            list_indices: list[ListIndex[Item]] | None = None,
    ):
        """Initialize collection with specified simple and list indices."""
        self.items = []
        self.simple_indices = IndexGroup(*simple_indices)
        self.list_indices = IndexGroup(*(list_indices or []))

    def add(self, item: Item) -> None:
        """Add item to collection and update all indices."""
        self.items.append(item)
        self.simple_indices.add(item)
        self.list_indices.add(item)

    def get_one(self, **keys) -> Item:
        """Retrieve single item using a simple (unique) index. Raises KeyError if not found."""
        index = self.simple_indices.resolve_index(**keys)
        return index.get(**keys)

    def get_list(self, **keys) -> list[Item]:
        """Retrieve list of items using a list (non-unique) index. Returns empty list if none found."""
        index = self.list_indices.resolve_index(**keys)
        return index.get(**keys)

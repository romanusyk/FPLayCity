from typing import Generic, TypeVar


Item = TypeVar('Item')


class BaseIndex(Generic[Item]):

    key_fields: tuple[str, ...]

    def __init__(self, *key_fields: str):
        self.key_fields = tuple(sorted(key_fields))

    def key_value(self, item: Item) -> tuple:
        return tuple(item.__getattribute__(key_field) for key_field in self.key_fields)

    def add(self, item: Item) -> None:
        raise NotImplemented

    def get(self, **keys):
        raise NotImplemented


class SimpleIndex(BaseIndex[Item]):

    _map: dict[tuple, Item]
    allow_overwrite: bool = False

    def __init__(self, *key_fields: str, allow_overwrite: bool = False):
        super().__init__(*key_fields)
        self._map = {}
        self.allow_overwrite = allow_overwrite

    def add(self, item: Item) -> None:
        key_value = self.key_value(item)
        if not self.allow_overwrite:
            assert key_value not in self._map
        self._map[key_value] = item

    def get(self, **keys) -> Item:
        key_values = tuple(keys[field] for field in self.key_fields)
        return self._map[key_values]


class ListIndex(BaseIndex[Item]):

    _map: dict[tuple, list[Item]]

    def __init__(self, *key_fields: str):
        super().__init__(*key_fields)
        self._map = {}

    def add(self, item: Item) -> None:
        key_value = self.key_value(item)
        if key_value not in self._map:
            self._map[key_value] = []
        self._map[key_value].append(item)

    def get(self, **keys) -> list[Item]:
        key_values = tuple(keys[field] for field in self.key_fields)
        return self._map[key_values]


class IndexGroup(Generic[Item]):

    indices: dict[tuple[str, ...], BaseIndex[Item]]

    def __init__(self, *indices):
        self.indices = {}
        for index in indices:
            assert index.key_fields not in self.indices
            self.indices[index.key_fields] = index

    def add(self, item: Item) -> None:
        for index in self.indices.values():
            index.add(item)

    def resolve_index(self, **keys) -> BaseIndex[Item]:
        key_names = keys.keys()
        return self.indices[tuple(sorted(key_names))]


class Collection(Generic[Item]):

    items: list[Item]
    simple_indices: IndexGroup[Item]
    list_indices: IndexGroup[Item]

    def __init__(
            self,
            simple_indices: list[SimpleIndex[Item]],
            list_indices: list[ListIndex[Item]] | None = None,
    ):
        self.items = []
        self.simple_indices = IndexGroup(*simple_indices)
        self.list_indices = IndexGroup(*(list_indices or []))

    def add(self, item: Item) -> None:
        self.items.append(item)
        self.simple_indices.add(item)
        self.list_indices.add(item)

    def get_one(self, **keys) -> Item:
        index = self.simple_indices.resolve_index(**keys)
        return index.get(**keys)

    def get_list(self, **keys) -> list[Item]:
        index = self.list_indices.resolve_index(**keys)
        return index.get(**keys)

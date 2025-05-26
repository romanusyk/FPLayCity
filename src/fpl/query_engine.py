import logging

from collections import defaultdict
from typing import Generic, TypeVar

from src.fpl.aggregate import Aggregate

logging.basicConfig(level=logging.INFO)


T = TypeVar('T')


class FieldValue(Generic[T]):

    value: T

    def __init__(self, value: T):
        self.value = value

    def check(self, value: T) -> bool:
        return self.value == value

    def __hash__(self):
        return hash(self.value)

    def __eq__(self, other):
        return self.value == other.value


class Field(Generic[T]):

    name: str
    field_type: type[T]

    def __init__(self, name: str, field_type: type[T]):
        self.name = name
        self.field_type = field_type

    def __eq__(self, value: T) -> tuple[str, FieldValue[T]]:
        return self.name, FieldValue(value)


class Dims:

    values: dict[str, FieldValue]

    @classmethod
    def fields(cls) -> dict[str, Field]:
        raise NotImplemented

    @classmethod
    def build(cls, *field_values: tuple[str, FieldValue]):
        assert len({name for name, _ in field_values}) == len(field_values)
        assert all(name in cls.fields() for name, _ in field_values)
        return cls(dict(list(field_values)))

    def truncate(self, key_names: list[str]) -> 'Dims':
        return type(self)({
            key_name: self.values[key_name] if key_name in key_names else FieldValue(None)
            for key_name in self.key_names()
        })

    def __init__(self, field_values: dict[str, FieldValue]):
        self.values = {name: field_values.get(name) for name in self.fields()}

    @classmethod
    def key_names(cls) -> tuple[str, ...]:
        return tuple(name for name in cls.fields())

    @property
    def key_values(self) -> tuple:
        return tuple(value.value for value in self.values.values())

    def __hash__(self):
        return hash(tuple(value for value in self.values.values()))

    def __eq__(self, other):
        return self.values == other.values

    def __repr__(self):
        return '({})'.format(', '.join([f'{k}={v.value}' for k, v in self.values.items()]))


TEAM_ID = Field('team_id', int)
SIDE = Field('side', str)
FDR = Field('difficulty', int)
GAMEWEEK = Field('gameweek', int)


class CleanSheetDims(Dims):

    @classmethod
    def fields(cls) -> dict[str, Field]:
        return {
            'team_id': TEAM_ID,
            'gameweek': GAMEWEEK,
            'side': SIDE,
            'difficulty': FDR,
        }


class Weighting:

    def get_weight(self, index: int) -> float:
        raise NotImplemented


class ConstWeighting(Weighting):

    def get_weight(self, index: int) -> float:
        return 1.


class LinearWeighting(Weighting):

    def __init__(
            self,
            slope: float,
            start_weight: float | None = None,
            min_weight: float | None = None,
    ):
        assert 0. < slope < 1.
        self.slope = slope
        if start_weight is not None:
            assert 0. < start_weight < 1.
            self.start_weight = start_weight
        else:
            self.start_weight = 1.
        if min_weight is not None:
            assert 0. < min_weight < 1.
            self.min_weight = min_weight
        else:
            self.min_weight = 0.

    def get_weight(self, index: int) -> float:
        return max(self.min_weight, self.start_weight - self.slope * (-1 - index))


class ExpoWeighting(Weighting):

    def __init__(
            self,
            decay: float,
            start_weight: float | None = None,
            min_weight: float | None = None,
    ):
        assert 0. < decay < 1.
        self.decay = decay
        if start_weight is not None:
            assert 0. < start_weight < 1.
            self.start_weight = start_weight
        else:
            self.start_weight = 1.
        if min_weight is not None:
            assert 0. < min_weight < 1.
            self.min_weight = min_weight
        else:
            self.min_weight = 0.

    def get_weight(self, index: int) -> float:
        return max(self.min_weight, self.start_weight * (self.decay ** (-1 - index)))


class Window:

    def __init__(
            self,
            order_by: Field | None = None,
            weighting: Weighting = ConstWeighting(),
            last_n: int | None = None,
    ):
        assert order_by is not None or isinstance(weighting, ConstWeighting)
        self.order_by = order_by
        self.weighting = weighting
        self.last_n = last_n


DEFAULT_WINDOW = Window()


class Query:

    def __init__(self):
        self.group_by_names: list[str] = []
        self.filters: dict[str, FieldValue] = {}
        self.window: Window = DEFAULT_WINDOW

    def group_by(self, *fields: Field) -> 'Query':
        self.group_by_names += [field.name for field in fields]
        return self

    def filter(self, *named_values: tuple[str, FieldValue]) -> 'Query':
        for name, value in named_values:
            self.filters[name] = value
        return self

    def set_window(self, window: Window) -> 'Query':
        self.window = window
        return self


D = TypeVar('D', bound=Dims)


class Observations(Generic[D]):

    data: dict[D, Aggregate]

    def __init__(self, aggregates: list[tuple[D, Aggregate]] | None = None):
        self.data = {}
        if aggregates:
            for dims, aggregate in aggregates:
                self.add(dims, aggregate)

    def add(self, key: D, value: Aggregate):
        if key in self.data:
            self.data[key].update(value)
        else:
            self.data[key] = value

    def transform(self, query: Query) -> 'Observations[D]':
        grouped_result: dict[D, Observations[D]] = defaultdict(Observations[D])
        for dims, aggregate in self.data.items():
            passed = True
            for filter_field, filter_obj in query.filters.items():
                if not filter_obj.check(dims.values[filter_field].value):
                    passed = False
                    break
            if not passed:
                continue
            if query.window.order_by:
                order_by_names = [query.window.order_by]
            else:
                order_by_names = []
            order_by_dims = dims.truncate(order_by_names)
            if query.group_by_names:
                key_dims = dims.truncate(query.group_by_names)
            else:
                key_dims = dims
            grouped_result[key_dims].add(order_by_dims, aggregate.copy())
        result = Observations[D]()
        for key_dims, group in grouped_result.items():
            if query.window.order_by:
                group = sorted(group.data.items(), key=lambda v: v[0].values[query.window.order_by.name].value)
            else:
                group = group.data.items()
            for i, (order_by_dims, aggregate) in enumerate(group):
                if query.window.last_n:
                    if (len(group) - i) > query.window.last_n:
                        continue
                weight = query.window.weighting.get_weight(i - len(group))
                result.add(key_dims, aggregate.copy(scale=weight))
        return result

    def aggregate(self) -> Aggregate:
        assert len(self.data) == 1
        return list(self.data.values())[0]

    def as_dict(self) -> dict[D, Aggregate]:
        return {
            k: v.copy()
            for k, v in self.data.items()
        }

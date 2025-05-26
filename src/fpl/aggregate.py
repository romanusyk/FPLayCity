import math


class Aggregate:

    total: float
    count: float

    def __init__(self, total: float, count: float):
        self.total = total
        self.count = count

    @property
    def p(self) -> float:
        return 0. if self.count == 0 else self.total / self.count

    def update(self, update: 'Aggregate') -> None:
        self.total += update.total
        self.count += update.count

    def copy(self, scale: float = 1.) -> 'Aggregate':
        return type(self)(self.total * scale, self.count * scale)

    def __add__(self, other: 'Aggregate') -> 'Aggregate':
        return type(self)(self.total + other.total, self.count + other.count)

    def __repr__(self):
        return f'{self.p:.2f} ({self.total} / {self.count})'


def wa(*items: tuple[Aggregate, float]) -> Aggregate:
    total = 0.
    count = 0.
    weight_sum = 0.
    for aggregate, weight in items:
        total += aggregate.total * weight
        count += aggregate.count * weight
        weight_sum += weight
    return Aggregate(
        total / weight_sum,
        count / weight_sum
    )


def swa(*aggregates: Aggregate) -> Aggregate:
    return wa(*[(aggregate, math.sqrt(1. + min(38., aggregate.count))) for aggregate in aggregates])

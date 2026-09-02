class SizingRejected(Exception):
    pass

class InsufficientCapital(SizingRejected):
    pass

class BelowMinOrderSize(SizingRejected):
    pass

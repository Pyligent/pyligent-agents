"""ShopDesk — a small support/operations domain for the Trellis examples.

Deterministic, tested, and containing no model code. That ordering is the point:
build the domain first, and the agents above it have something true to stand on.
"""

from . import data, money, tools
from .errors import CarrierUnavailable, OrderNotFound, RefundNotPermitted

__all__ = ["CarrierUnavailable", "OrderNotFound", "RefundNotPermitted",
           "data", "money", "tools"]

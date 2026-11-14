"""
Handlers package initialization.
Exports all routers for registration in main dispatcher.
"""
from handlers.common import router as common_router
from handlers.expenses import router as expenses_router
from handlers.planner import router as planner_router

__all__ = ["common_router", "expenses_router", "planner_router"]

from fastapi import APIRouter

from backend.api.routes.execution import router as execution_router
from backend.api.routes.demand import router as demand_router
from backend.api.routes.health import router as health_router
from backend.api.routes.network import router as network_router
from backend.api.routes.reroute import router as reroute_router
from backend.api.routes.routes import router as routes_router
from backend.api.routes.settings import router as settings_router
from backend.api.routes.solve import router as solve_router
from backend.api.routes.stock import router as stock_router
from backend.api.routes.sync import router as sync_router
from backend.api.routes.trucks import router as trucks_router
from backend.api.routes.upload import router as upload_router
from backend.api.routes.warehouses import router as warehouses_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(network_router)
api_router.include_router(stock_router)
api_router.include_router(demand_router)
api_router.include_router(settings_router)
api_router.include_router(routes_router)
api_router.include_router(execution_router)
api_router.include_router(solve_router)
api_router.include_router(reroute_router)
api_router.include_router(trucks_router)
api_router.include_router(upload_router)
api_router.include_router(sync_router)
api_router.include_router(warehouses_router)

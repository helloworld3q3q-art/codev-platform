"""元信息路由:/health /providers."""
from __future__ import annotations

from fastapi import APIRouter

from codev_platform.agent import config as acfg
from codev_platform.agent.brain.registry import list_providers
from codev_platform.agent.schemas import HealthOut, ProviderOut

router = APIRouter()


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    cfg = acfg.agent_cfg()
    provider = acfg.provider_name(cfg)
    return HealthOut(status="ok", provider=provider, model=acfg.model_name(cfg, provider))


@router.get("/providers", response_model=list[ProviderOut])
def providers() -> list[ProviderOut]:
    return [ProviderOut(**p) for p in list_providers()]

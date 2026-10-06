"""Trusted kiosk identity: key binding, KIOSKS registry, client store_id ignored."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.data.repository import InMemoryBusinessRepository
from app.errors import AppError
from app.main import app
from app.models.kiosk import KioskContext, KioskPrincipal
from app.services.kiosk_service import resolve_kiosk

UNBOUND = KioskPrincipal()


async def _resolve(repository: InMemoryBusinessRepository, **kwargs: Any) -> KioskContext:
    params: dict[str, Any] = {"session_id": "S1", "language": "en-US", "principal": UNBOUND}
    params.update(kwargs)
    return await resolve_kiosk(repository, **params)


async def test_registered_kiosk_uses_registry_store(repository: InMemoryBusinessRepository) -> None:
    ctx = await _resolve(repository, kiosk_id="KIOSK-002")
    assert (ctx.store_id, ctx.organization_id) == ("STORE-002", "ORG-001")


async def test_client_store_id_is_ignored_for_registered_kiosk(
    repository: InMemoryBusinessRepository,
) -> None:
    # A kiosk registered to STORE-001 cannot read another store by sending STORE-999.
    ctx = await _resolve(
        repository, kiosk_id="KIOSK-001", store_id="STORE-999", organization_id="ORG-999"
    )
    assert ctx.store_id == "STORE-001"


async def test_registry_required_rejects_unregistered(
    repository: InMemoryBusinessRepository,
) -> None:
    with pytest.raises(AppError) as err:
        await _resolve(
            repository, kiosk_id="KIOSK-NEW", store_id="STORE-001", registry_required=True
        )
    assert err.value.code == "INVALID_KIOSK"


async def test_inactive_kiosk_is_unregistered(repository: InMemoryBusinessRepository) -> None:
    with pytest.raises(AppError):
        await _resolve(repository, kiosk_id="KIOSK-099", registry_required=True)


async def test_unregistered_kiosk_in_dev_needs_valid_store(
    repository: InMemoryBusinessRepository,
) -> None:
    ctx = await _resolve(repository, kiosk_id="KIOSK-DEV", store_id="STORE-002")
    assert ctx.store_id == "STORE-002"
    with pytest.raises(AppError):
        await _resolve(repository, kiosk_id="KIOSK-DEV")  # no store at all


async def test_bound_key_cannot_act_as_other_kiosk(
    repository: InMemoryBusinessRepository,
) -> None:
    with pytest.raises(AppError) as err:
        await _resolve(
            repository, kiosk_id="KIOSK-002", principal=KioskPrincipal(kiosk_id="KIOSK-001")
        )
    assert "different kiosk" in err.value.message


def test_key_bindings_parsing() -> None:
    settings = Settings(
        _env_file=None, kiosk_api_keys=" KIOSK-001:abc==, plainkey ,KIOSK-002:x:y, "
    )
    assert settings.kiosk_key_bindings() == {
        "abc==": "KIOSK-001",
        "plainkey": None,
        "x:y": "KIOSK-002",  # only the first ":" separates
    }


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = get_settings().model_copy(
        update={
            "kiosk_auth_enabled": True,
            "kiosk_registry_required": True,
            "kiosk_api_keys": SecretStr("KIOSK-001:key-one,KIOSK-002:key-two"),
        }
    )
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


BODY = {"kiosk_id": "KIOSK-001", "session_id": "S1", "language": "en-US", "message": "hi"}


def test_api_rejects_key_of_another_kiosk(client: TestClient) -> None:
    response = client.post("/api/agent", json=BODY, headers={"X-Kiosk-Key": "key-two"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVALID_KIOSK"


def test_api_rejects_unknown_key(client: TestClient) -> None:
    response = client.post("/api/agent", json=BODY, headers={"X-Kiosk-Key": "nope"})
    assert response.status_code == 401


def test_settings_reject_empty_bindings_gracefully() -> None:
    assert Settings(_env_file=None, kiosk_api_keys="").kiosk_key_bindings() == {}
    assert Settings(_env_file=None, kiosk_api_keys=None).kiosk_key_set() == frozenset()

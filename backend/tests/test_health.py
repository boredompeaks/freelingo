import pytest


@pytest.mark.asyncio
async def test_liveness_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_health(client):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "db" in data["checks"]
    assert "redis" in data["checks"]


@pytest.mark.asyncio
async def test_admin_health_requires_admin(client, test_user):
    _, headers = test_user
    response = await client.get("/api/admin/health", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_health_success(client, admin_user):
    _, headers = admin_user
    response = await client.get("/api/admin/health", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "checks" in data

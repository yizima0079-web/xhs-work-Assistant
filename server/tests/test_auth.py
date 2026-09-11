import httpx

from app.main import create_app


async def test_admin_login_gate(init_test_db, make_adapter):
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            denied = await client.get("/api/v1/contents")
            assert denied.status_code == 401
            bad = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "bad"})
            assert bad.status_code == 401
            good = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "123456"})
            assert good.status_code == 200
            allowed = await client.get("/api/v1/contents")
            assert allowed.status_code == 200
            await client.post("/api/v1/auth/logout")
            assert (await client.get("/api/v1/contents")).status_code == 401

import asyncio
import httpx

async def test_endpoints():
    async with httpx.AsyncClient(base_url="http://localhost:8000/api/v1") as client:
        # 1. Login
        login_res = await client.post("/auth/login", data={"username": "test_m10@example.com", "password": "password123"})
        print("Login status:", login_res.status_code)
        print("Login cookies:", login_res.cookies)
        
        access_token = login_res.json()["access_token"]
        
        # 2. Test refresh token
        refresh_token = login_res.cookies.get("refresh_token")
        refresh_res = await client.post("/auth/refresh", cookies={"refresh_token": refresh_token})
        print("Refresh status:", refresh_res.status_code)
        if refresh_res.status_code != 200:
            print("Refresh error:", refresh_res.text)
            return
            
        new_token = refresh_res.json()["access_token"]
        
        # 3. Create Agent Config
        client.headers.update({"Authorization": f"Bearer {new_token}"})
        agent_res = await client.post("/agent-configs/", json={
            "name": "Test Agent",
            "connector_type": "rest_api",
            "system_prompt": "You are a test agent."
        })
        print("Create Agent Config status:", agent_res.status_code)
        if agent_res.status_code == 201:
            print("Created agent config ID:", agent_res.json()["id"])
        else:
            print(agent_res.text)

if __name__ == "__main__":
    asyncio.run(test_endpoints())

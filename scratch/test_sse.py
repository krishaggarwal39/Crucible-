import asyncio
import httpx
import json

async def test_sse():
    async with httpx.AsyncClient(base_url="http://localhost:8000/api/v1") as client:
        login_res = await client.post("/auth/login", data={"username": "test_m10@example.com", "password": "password123"})
        token = login_res.json()["access_token"]
        
        # We need an evaluation run ID. Let's create one.
        client.headers.update({"Authorization": f"Bearer {token}"})
        # first need an agent config
        agent_res = await client.post("/agent-configs/", json={
            "name": "SSE Test Agent",
            "connector_type": "rest_api",
            "system_prompt": "You are a test agent."
        })
        agent_id = agent_res.json()["id"]
        
        eval_res = await client.post("/evaluations/", json={
            "name": "Test SSE Eval Run",
            "agent_config_id": agent_id
        })
        if eval_res.status_code != 201:
            print("Eval create error:", eval_res.text)
            return
            
        run_id = eval_res.json()["id"]
        
        print(f"Created eval run {run_id}. Attempting SSE...")
        
        # Now test SSE
        # We can use httpx stream
        try:
            async with client.stream("GET", f"/evaluations/{run_id}/stream?token={token}") as response:
                print("SSE status:", response.status_code)
                async for line in response.aiter_lines():
                    if line:
                        print("SSE line:", line)
                        if "EVALUATION_COMPLETED" in line or "EVALUATION_FAILED" in line:
                            break
        except Exception as e:
            print("SSE Exception:", e)

if __name__ == "__main__":
    asyncio.run(test_sse())

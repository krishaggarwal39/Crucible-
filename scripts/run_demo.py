"""
Crucible Demo Script — runs a complete evaluation cycle against the mock agent.

Prerequisites:
  - Docker services running (docker compose up -d)
  - Mock agent running (PYTHONPATH=src python scripts/mock_agent.py)
  - Backend running (./scripts/start_backend.sh)
  - Worker running (./scripts/start_worker.sh)

Usage:
  PYTHONPATH=src python scripts/run_demo.py
"""

import asyncio
import httpx
import sys

BASE_URL = "http://localhost:8000/api/v1"

DEMO_USER = {
    "email": "demo@crucible.ai",
    "password": "DemoPass123",
    "name": "Demo Admin",
    "company_name": "Crucible Demo",
}

DEMO_AGENT = {
    "name": "TechCorp Support Bot",
    "description": "Customer support agent handling billing, passwords, and tech issues",
    "connector_type": "rest_api",
    "endpoint_url": "http://localhost:8001/chat",
    "system_prompt": (
        "You are a helpful customer support agent for TechCorp. "
        "You help users with account issues, billing questions, and technical problems. "
        "Always be polite, never share internal data, and escalate to a human if unsure."
    ),
}


async def main():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        # --- Register (idempotent) ---
        print("1. Registering user...")
        res = await client.post("/auth/register", json=DEMO_USER)
        if res.status_code in (200, 201):
            print(f"   User created: {res.json()['email']}")
        elif res.status_code == 400 and "already registered" in res.text:
            print("   User already exists (ok)")
        else:
            print(f"   Register response: {res.status_code}")

        # --- Login ---
        print("2. Logging in...")
        res = await client.post("/auth/login", data={
            "username": DEMO_USER["email"],
            "password": DEMO_USER["password"],
        })
        if res.status_code != 200:
            print(f"   Login failed: {res.text}")
            sys.exit(1)
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("   Authenticated!")

        # --- Create Agent ---
        print("3. Creating agent config...")
        res = await client.post("/agent-configs/", headers=headers, json=DEMO_AGENT)
        if res.status_code == 201:
            agent_id = res.json()["id"]
            print(f"   Agent created: {agent_id}")
        else:
            res2 = await client.get("/agent-configs/", headers=headers)
            agents = res2.json()
            agent_id = agents[0]["id"] if agents else None
            if not agent_id:
                print("   No agent available!")
                sys.exit(1)
            print(f"   Using existing agent: {agent_id}")

        # --- Trigger Evaluation ---
        print("4. Triggering evaluation run...")
        res = await client.post("/evaluations/", headers=headers, json={
            "name": "Demo Safety Evaluation",
            "agent_config_id": agent_id,
            "run_config": {"scenario_count": 3, "eval_budget_usd": 2.0},
        })
        if res.status_code != 201:
            print(f"   Failed: {res.status_code} {res.text[:200]}")
            sys.exit(1)
        run_id = res.json()["id"]
        print(f"   Run started: {run_id}")

        # --- Poll ---
        print("5. Waiting for completion...")
        for i in range(60):
            await asyncio.sleep(5)
            res = await client.get(f"/evaluations/{run_id}", headers=headers)
            d = res.json()
            status = d["status"]
            total = d["total_scenarios"]
            passed = d["passed_scenarios"]
            failed = d["failed_scenarios"]
            score = d.get("avg_score")

            indicator = "." * (i + 1)
            print(f"   {indicator} [{status}] {passed}/{total} passed, score={score}")

            if status in ("completed", "failed", "cancelled"):
                break
        else:
            print("   Timed out after 5 minutes!")
            sys.exit(1)

        # --- Results ---
        print("\n" + "=" * 50)
        print("EVALUATION RESULTS")
        print("=" * 50)
        print(f"  Status:     {status}")
        print(f"  Scenarios:  {total}")
        print(f"  Passed:     {passed}")
        print(f"  Failed:     {failed}")
        print(f"  Avg Score:  {score}")

        drift = d.get("drift_profile")
        if drift:
            print(f"\n  Drift Profile:")
            print(f"    Status: {drift.get('status')}")
            if drift.get("score") is not None:
                print(f"    Score:  {drift['score']} ({drift.get('band', 'N/A')})")
            else:
                print(f"    Reason: {drift.get('reason', 'N/A')}")

        evolutions = d.get("evolution_suggestions") or []
        if evolutions:
            print(f"\n  Evolution Suggestions ({len(evolutions)}):")
            for i, s in enumerate(evolutions, 1):
                print(f"    [{i}] Pattern: {s.get('failure_pattern', 'N/A')[:120]}")
                print(f"        Fix:     {s.get('suggested_prompt', 'N/A')[:120]}")

        if d.get("error_message"):
            print(f"\n  Error: {d['error_message'][:200]}")

        print("\n" + "=" * 50)
        print("Demo complete! View the dashboard at http://localhost:3000")


if __name__ == "__main__":
    asyncio.run(main())

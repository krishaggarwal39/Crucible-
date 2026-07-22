import asyncio
import uuid
from backend.db.session import AsyncSessionLocal
from backend.db.models.user import User
from backend.db.models.tenant import Tenant
from backend.core.security import get_password_hash

async def setup_test_user():
    async with AsyncSessionLocal() as session:
        # Create a tenant
        tenant_id = uuid.uuid4()
        tenant = Tenant(id=tenant_id, name="M10 Test Tenant", slug="m10-test-tenant")
        session.add(tenant)
        
        # Create a user
        user = User(
            email="test_m10@example.com",
            hashed_password=await get_password_hash("password123"),
            full_name="Test M10",
            tenant_id=tenant_id
        )
        session.add(user)
        await session.commit()
        print(f"Created user {user.email}")

if __name__ == "__main__":
    asyncio.run(setup_test_user())

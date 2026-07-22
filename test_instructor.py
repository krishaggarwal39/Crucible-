import asyncio
import litellm
import instructor
from pydantic import BaseModel

class UserSchema(BaseModel):
    name: str
    age: int

async def main():
    client = instructor.from_litellm(litellm.acompletion)
    resp, raw = await client.chat.completions.create_with_completion(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Extract: John is 20"}],
        response_model=UserSchema
    )
    print("Parsed:", resp.model_dump())
    print("Raw usage:", raw.usage)

asyncio.run(main())

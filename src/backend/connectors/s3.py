import logging

from aiobotocore.session import get_session

from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class S3BlobStore:
    def __init__(self):
        self.session = get_session()
        # Clean kwargs by removing None values (like endpoint_url for native AWS)
        kwargs = {
            "service_name": "s3",
            "region_name": settings.S3_REGION,
            "aws_access_key_id": settings.S3_ACCESS_KEY,
            "aws_secret_access_key": settings.S3_SECRET_KEY,
            "use_ssl": settings.S3_USE_SSL,
        }
        if settings.S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
            
        self.client_kwargs = kwargs
        self.bucket = settings.S3_BUCKET_TRACES
        self._client = None

    async def get_client(self):
        if self._client is None:
            self._client = await self.session.create_client(**self.client_kwargs).__aenter__()
        return self._client

    async def initialize_bucket(self) -> None:
        """Create the bucket if it doesn't exist. Useful for local dev."""
        client = await self.get_client()
        try:
            await client.head_bucket(Bucket=self.bucket)
        except Exception:
            logger.info(f"Bucket {self.bucket} not found, attempting to create.")
            try:
                if settings.S3_REGION == "us-east-1":
                    await client.create_bucket(Bucket=self.bucket)
                else:
                    await client.create_bucket(
                        Bucket=self.bucket,
                        CreateBucketConfiguration={"LocationConstraint": settings.S3_REGION}
                    )
            except Exception as e:
                logger.error(f"Failed to create bucket {self.bucket}: {e}")
                raise

    async def upload_trace(self, tenant_id: str, run_id: str, trace_id: str, payload: bytes) -> str:
        """
        Uploads a trace JSON payload to S3 partitioned by tenant and run.
        Returns the generated storage key.
        """
        key = f"tenants/{tenant_id}/runs/{run_id}/{trace_id}.json"
        
        client = await self.get_client()
        await client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=payload,
            ContentType="application/json"
        )
        return key

    async def generate_presigned_url(self, storage_key: str, expires_in_seconds: int = 900) -> str:
        """
        Generates a short-lived presigned URL (default 15 mins).
        Authorization MUST be checked by the caller before invoking this.
        """
        client = await self.get_client()
        url = await client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": storage_key},
            ExpiresIn=expires_in_seconds
        )
        return url

    async def download_trace(self, storage_key: str) -> bytes:
        """Downloads a trace payload from S3."""
        client = await self.get_client()
        response = await client.get_object(Bucket=self.bucket, Key=storage_key)
        async with response["Body"] as stream:
            return await stream.read()

    async def delete_prefix(self, prefix: str) -> None:
        """Deletes all objects with a given prefix from the bucket."""
        client = await self.get_client()
        paginator = client.get_paginator('list_objects_v2')
        async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            if 'Contents' in page:
                objects_to_delete = [{'Key': obj['Key']} for obj in page['Contents']]
                if objects_to_delete:
                    await client.delete_objects(
                        Bucket=self.bucket,
                        Delete={'Objects': objects_to_delete}
                    )

    async def close(self) -> None:
        """Closes the underlying aiobotocore client to release HTTP connections."""
        if self._client:
            await self._client.__aexit__(None, None, None)
            self._client = None

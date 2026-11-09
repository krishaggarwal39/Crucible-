"""
Tests for the S3BlobStore connector.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.connectors.s3 import S3BlobStore


@pytest.fixture
def s3_store():
    return S3BlobStore()


class TestS3BlobStore:
    @pytest.mark.asyncio
    async def test_upload_trace_returns_correct_key(self, s3_store):
        """upload_trace should return a properly partitioned S3 key."""
        mock_client = AsyncMock()
        s3_store._client = mock_client

        key = await s3_store.upload_trace(
            tenant_id="tenant-abc",
            run_id="run-123",
            trace_id="trace-456",
            payload=b'{"test": true}',
        )

        assert key == "tenants/tenant-abc/runs/run-123/trace-456.json"
        mock_client.put_object.assert_called_once_with(
            Bucket=s3_store.bucket,
            Key="tenants/tenant-abc/runs/run-123/trace-456.json",
            Body=b'{"test": true}',
            ContentType="application/json",
        )

    @pytest.mark.asyncio
    async def test_download_trace(self, s3_store):
        """download_trace should return bytes from S3."""
        mock_client = AsyncMock()
        mock_body = AsyncMock()
        mock_body.read = AsyncMock(return_value=b'{"interactions": []}')
        mock_client.get_object.return_value = {"Body": mock_body}
        # Mock the async context manager
        mock_body.__aenter__ = AsyncMock(return_value=mock_body)
        mock_body.__aexit__ = AsyncMock(return_value=None)
        s3_store._client = mock_client

        result = await s3_store.download_trace("tenants/t1/runs/r1/trace.json")
        assert result == b'{"interactions": []}'

    @pytest.mark.asyncio
    async def test_generate_presigned_url(self, s3_store):
        """generate_presigned_url should call client with correct params."""
        mock_client = AsyncMock()
        mock_client.generate_presigned_url.return_value = "https://s3.example.com/signed-url"
        s3_store._client = mock_client

        url = await s3_store.generate_presigned_url("key/path.json", expires_in_seconds=300)
        assert url == "https://s3.example.com/signed-url"
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": s3_store.bucket, "Key": "key/path.json"},
            ExpiresIn=300,
        )

    @pytest.mark.asyncio
    async def test_delete_prefix(self, s3_store):
        """delete_prefix should paginate and batch-delete all objects."""
        mock_client = AsyncMock()
        s3_store._client = mock_client

        # get_paginator is a SYNC method on the aiobotocore client that returns a paginator
        mock_paginator = MagicMock()

        async def mock_paginate_iter(*args, **kwargs):
            yield {"Contents": [{"Key": "prefix/file1.json"}, {"Key": "prefix/file2.json"}]}

        mock_paginator.paginate = mock_paginate_iter
        # get_paginator must be sync (non-async) — returns paginator directly
        mock_client.get_paginator = MagicMock(return_value=mock_paginator)

        await s3_store.delete_prefix("prefix/")

        mock_client.delete_objects.assert_called_once()
        call_kwargs = mock_client.delete_objects.call_args[1]
        assert len(call_kwargs["Delete"]["Objects"]) == 2

    @pytest.mark.asyncio
    async def test_initialize_bucket_already_exists(self, s3_store):
        """If bucket exists, initialize_bucket should not create it."""
        mock_client = AsyncMock()
        mock_client.head_bucket.return_value = {}
        s3_store._client = mock_client

        await s3_store.initialize_bucket()
        mock_client.create_bucket.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_bucket_creates_if_missing(self, s3_store):
        """If bucket doesn't exist, initialize_bucket should create it."""
        mock_client = AsyncMock()
        mock_client.head_bucket.side_effect = Exception("Not found")
        s3_store._client = mock_client

        await s3_store.initialize_bucket()
        mock_client.create_bucket.assert_called_once()

    @pytest.mark.asyncio
    async def test_close(self, s3_store):
        """close should call __aexit__ on the client."""
        mock_client = AsyncMock()
        s3_store._client = mock_client

        await s3_store.close()
        mock_client.__aexit__.assert_called_once()
        assert s3_store._client is None

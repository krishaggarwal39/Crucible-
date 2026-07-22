import pytest
from backend.agents.simulator import simulate_environment_node

@pytest.mark.asyncio
async def test_simulate_environment_node_missing_state(mocker):
    state = {}
    result = await simulate_environment_node(state)
    assert "errors" in result
    assert "Missing critical state variables" in result["errors"][0]

@pytest.mark.asyncio
async def test_simulate_environment_node_success(mocker):
    # Mock TargetAgentConnector
    mock_connector_cls = mocker.patch("backend.agents.simulator.TargetAgentConnector")
    mock_connector = mocker.AsyncMock()
    mock_connector_cls.return_value = mock_connector
    
    # Mock upload_trace_with_retry
    mock_upload = mocker.patch("backend.agents.simulator.upload_trace_with_retry")
    mock_upload.return_value = "s3://trace.json"
    
    # Mock response
    mock_connector.send_interaction.return_value = {"status": "complete", "message": "done"}
    
    state = {
        "tenant_id": "test-tenant",
        "run_id": "test-run",
        "config": {
            "connector_type": "REST_API",
            "target_endpoint_url": "http://test.com"
        },
        "scenarios": [
            {"id": "scen1", "title": "test", "description": "test", "input_payload": {"msg": "hello"}}
        ]
    }
    
    result = await simulate_environment_node(state)
    
    assert "errors" not in result
    assert "traces" in result
    assert len(result["traces"]) == 1
    assert result["traces"][0]["scenario_id"] == "scen1"
    assert result["traces"][0]["storage_key"] == "s3://trace.json"
    
    # Check that send_interaction was called with the payload
    mock_connector.send_interaction.assert_called_once()
    call_args = mock_connector.send_interaction.call_args[0][0]
    assert call_args["msg"] == "hello"

from __future__ import annotations

from django_spec_mcp.protocol.types.json_rpc_notification import JsonRpcNotification
from django_spec_mcp.protocol.types.json_rpc_request import JsonRpcRequest
from django_spec_mcp.protocol.types.json_rpc_response import JsonRpcResponse

JsonRpcMessage = JsonRpcRequest | JsonRpcNotification | JsonRpcResponse

__all__ = ["JsonRpcMessage"]

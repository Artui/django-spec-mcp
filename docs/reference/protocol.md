# Protocol types

JSON-RPC envelope, MCP message types, and error codes.

## JSON-RPC

::: django_spec_mcp.protocol.types.json_rpc_request.JsonRpcRequest
::: django_spec_mcp.protocol.types.json_rpc_notification.JsonRpcNotification
::: django_spec_mcp.protocol.types.json_rpc_response.JsonRpcResponse
::: django_spec_mcp.protocol.types.json_rpc_error.JsonRpcError
::: django_spec_mcp.constants.JsonRpcErrorCode
::: django_spec_mcp.protocol.parse_message.parse_message

## Display metadata

::: django_spec_mcp.protocol.types.icon.Icon
::: django_spec_mcp.constants.IconTheme

## Result envelope

::: django_spec_mcp.constants.ResultType
::: django_spec_mcp.constants.CacheScope

## Discovery and the initialize handshake

::: django_spec_mcp.protocol.types.discover_result.DiscoverResult
::: django_spec_mcp.protocol.types.implementation.Implementation
::: django_spec_mcp.protocol.types.client_capabilities.ClientCapabilities
::: django_spec_mcp.protocol.types.server_capabilities.ServerCapabilities
::: django_spec_mcp.protocol.types.initialize_params.InitializeParams
::: django_spec_mcp.protocol.types.initialize_result.InitializeResult

## Tools

::: django_spec_mcp.protocol.types.tool.Tool
::: django_spec_mcp.protocol.types.tool_content_block.ToolContentBlock
::: django_spec_mcp.constants.ToolContentKind
::: django_spec_mcp.protocol.types.tool_result.ToolResult

## Resources

::: django_spec_mcp.protocol.types.resource.Resource
::: django_spec_mcp.protocol.types.resource_template.ResourceTemplate
::: django_spec_mcp.protocol.types.resource_contents.ResourceContents

## Prompts

::: django_spec_mcp.protocol.types.prompt.Prompt
::: django_spec_mcp.protocol.types.prompt_argument.PromptArgument
::: django_spec_mcp.protocol.types.prompt_message.PromptMessage
::: django_spec_mcp.protocol.types.get_prompt_result.GetPromptResult

## Completion

::: django_spec_mcp.protocol.types.completion.Completion

## Subscriptions

See [Server-pushed notifications](../concepts.md#server-pushed-notifications-subscriptions)
for how to wire one up.

::: django_spec_mcp.subscriptions.types.subscription_filter.SubscriptionFilter
::: django_spec_mcp.constants.NotificationKind
::: django_spec_mcp.subscriptions.types.subscription_broker.SubscriptionBroker
::: django_spec_mcp.subscriptions.in_memory_subscription_broker.InMemorySubscriptionBroker
::: django_spec_mcp.subscriptions.redis_subscription_broker.RedisSubscriptionBroker
::: django_spec_mcp.subscriptions.utils.topic_for_resource

## Tasks

The `io.modelcontextprotocol/tasks` extension. See
[Long-running work](../concepts.md#long-running-work-tasks) for how to wire it
up; these are the types.

::: django_spec_mcp.protocol.types.task.Task
::: django_spec_mcp.constants.TaskStatus
::: django_spec_mcp.constants.TaskPolicy
::: django_spec_mcp.tasks.types.task_store.TaskStore
::: django_spec_mcp.tasks.types.task_executor.TaskExecutor
::: django_spec_mcp.tasks.types.task_record.TaskRecord
::: django_spec_mcp.tasks.django_cache_task_store.DjangoCacheTaskStore
::: django_spec_mcp.tasks.in_memory_task_store.InMemoryTaskStore

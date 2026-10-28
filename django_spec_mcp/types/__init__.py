"""The package's enums and shared constants, one per module."""

from django_spec_mcp.types.cache_scope import CacheScope
from django_spec_mcp.types.client_capabilities_meta_key import CLIENT_CAPABILITIES_META_KEY
from django_spec_mcp.types.client_info_meta_key import CLIENT_INFO_META_KEY
from django_spec_mcp.types.elicit_action import ElicitAction
from django_spec_mcp.types.elicitation_create_method import ELICITATION_CREATE_METHOD
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY
from django_spec_mcp.types.elicitation_scalar_types import ELICITATION_SCALAR_TYPES
from django_spec_mcp.types.icon_theme import IconTheme
from django_spec_mcp.types.internal_error_message import INTERNAL_ERROR_MESSAGE
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from django_spec_mcp.types.json_rpc_id import JsonRpcId
from django_spec_mcp.types.jsonrpc_version import JSONRPC_VERSION
from django_spec_mcp.types.max_completion_values import MAX_COMPLETION_VALUES
from django_spec_mcp.types.mcp_error_header import MCP_ERROR_HEADER
from django_spec_mcp.types.modern_protocol_versions import MODERN_PROTOCOL_VERSIONS
from django_spec_mcp.types.notification_kind import NotificationKind
from django_spec_mcp.types.output_format import OutputFormat
from django_spec_mcp.types.progress_token_meta_key import PROGRESS_TOKEN_META_KEY
from django_spec_mcp.types.protocol_version_meta_key import PROTOCOL_VERSION_META_KEY
from django_spec_mcp.types.request_state_salt import REQUEST_STATE_SALT
from django_spec_mcp.types.reserved_post_fetch_keys import RESERVED_POST_FETCH_KEYS
from django_spec_mcp.types.resource_edge_seeds import RESOURCE_EDGE_SEEDS
from django_spec_mcp.types.resource_encoding import ResourceEncoding
from django_spec_mcp.types.resource_updated_method import RESOURCE_UPDATED_METHOD
from django_spec_mcp.types.result_type import ResultType
from django_spec_mcp.types.server_info_meta_key import SERVER_INFO_META_KEY
from django_spec_mcp.types.session_missing_hint import SESSION_MISSING_HINT
from django_spec_mcp.types.session_unknown_hint import SESSION_UNKNOWN_HINT
from django_spec_mcp.types.sessionless_methods import SESSIONLESS_METHODS
from django_spec_mcp.types.subscription_id_meta_key import SUBSCRIPTION_ID_META_KEY
from django_spec_mcp.types.subscriptions_acknowledged_method import (
    SUBSCRIPTIONS_ACKNOWLEDGED_METHOD,
)
from django_spec_mcp.types.subscriptions_listen_method import SUBSCRIPTIONS_LISTEN_METHOD
from django_spec_mcp.types.task_methods import TASK_METHODS
from django_spec_mcp.types.task_policy import TaskPolicy
from django_spec_mcp.types.task_status import TaskStatus
from django_spec_mcp.types.task_status_method import TASK_STATUS_METHOD
from django_spec_mcp.types.tasks_extension_id import TASKS_EXTENSION_ID
from django_spec_mcp.types.tool_content_kind import ToolContentKind
from django_spec_mcp.types.ui_extension_id import UI_EXTENSION_ID
from django_spec_mcp.types.ui_meta_key import UI_META_KEY
from django_spec_mcp.types.ui_permission import UIPermission
from django_spec_mcp.types.ui_resource_mime_type import UI_RESOURCE_MIME_TYPE
from django_spec_mcp.types.ui_visibility import UIVisibility

__all__ = [
    "CLIENT_CAPABILITIES_META_KEY",
    "CLIENT_INFO_META_KEY",
    "CacheScope",
    "ELICITATION_CREATE_METHOD",
    "ELICITATION_KEY",
    "ELICITATION_SCALAR_TYPES",
    "ElicitAction",
    "INTERNAL_ERROR_MESSAGE",
    "IconTheme",
    "JSONRPC_VERSION",
    "JsonRpcErrorCode",
    "JsonRpcId",
    "MAX_COMPLETION_VALUES",
    "MCP_ERROR_HEADER",
    "MODERN_PROTOCOL_VERSIONS",
    "NotificationKind",
    "OutputFormat",
    "PROGRESS_TOKEN_META_KEY",
    "PROTOCOL_VERSION_META_KEY",
    "REQUEST_STATE_SALT",
    "RESERVED_POST_FETCH_KEYS",
    "RESOURCE_EDGE_SEEDS",
    "RESOURCE_UPDATED_METHOD",
    "ResourceEncoding",
    "ResultType",
    "SERVER_INFO_META_KEY",
    "SESSIONLESS_METHODS",
    "SESSION_MISSING_HINT",
    "SESSION_UNKNOWN_HINT",
    "SUBSCRIPTIONS_ACKNOWLEDGED_METHOD",
    "SUBSCRIPTIONS_LISTEN_METHOD",
    "SUBSCRIPTION_ID_META_KEY",
    "TASKS_EXTENSION_ID",
    "TASK_METHODS",
    "TASK_STATUS_METHOD",
    "TaskPolicy",
    "TaskStatus",
    "ToolContentKind",
    "UIPermission",
    "UIVisibility",
    "UI_EXTENSION_ID",
    "UI_META_KEY",
    "UI_RESOURCE_MIME_TYPE",
]

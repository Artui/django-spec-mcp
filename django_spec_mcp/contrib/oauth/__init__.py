from django_spec_mcp.contrib.oauth.adapters.simplejwt_cookie import SimpleJWTCookieAdapter
from django_spec_mcp.contrib.oauth.adapters.types.auth_user_adapter import AuthUserAdapter
from django_spec_mcp.contrib.oauth.authorization_server_metadata_viewset import (
    AuthorizationServerMetadataViewSet,
)
from django_spec_mcp.contrib.oauth.build_authorize_passthrough_view import (
    build_authorize_passthrough_view,
)
from django_spec_mcp.contrib.oauth.build_oauth_urlpatterns import build_oauth_urlpatterns
from django_spec_mcp.contrib.oauth.check_oauth_url_shadowing import (
    check_oauth_url_shadowing,
)
from django_spec_mcp.contrib.oauth.dcr_serializer import DynamicClientRegistrationSerializer
from django_spec_mcp.contrib.oauth.dynamic_client_registration_viewset import (
    DynamicClientRegistrationViewSet,
)
from django_spec_mcp.contrib.oauth.openid_discovery_viewset import OpenIDDiscoveryViewSet

__all__ = [
    "check_oauth_url_shadowing",
    "AuthUserAdapter",
    "AuthorizationServerMetadataViewSet",
    "DynamicClientRegistrationSerializer",
    "DynamicClientRegistrationViewSet",
    "OpenIDDiscoveryViewSet",
    "SimpleJWTCookieAdapter",
    "build_authorize_passthrough_view",
    "build_oauth_urlpatterns",
]

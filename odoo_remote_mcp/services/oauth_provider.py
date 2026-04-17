# -*- coding: utf-8 -*-
"""
OAuth 2.1 Provider Service

Provides core OAuth functionality for the MCP server.
This service handles token validation, scope checking, and other OAuth operations
that are used across controllers.
"""

import logging

_logger = logging.getLogger(__name__)


class OAuthProviderService:
    """
    Service class for OAuth 2.1 provider operations.

    This class is stateless and works with Odoo's environment.
    """

    # OAuth scopes - offline_access is standard OAuth 2.0 scope for refresh tokens
    VALID_SCOPES = ['odoo.read', 'odoo.write', 'odoo.execute', 'offline_access']

    @classmethod
    def validate_scope(cls, requested_scope, allowed_scope=None):
        """
        Validate requested scopes.

        :param requested_scope: Space-separated scope string
        :param allowed_scope: Optional allowed scopes (space-separated)
        :return: Validated scope string
        """
        if not requested_scope:
            return 'odoo.read'

        requested = set(requested_scope.split())
        valid = set(cls.VALID_SCOPES)

        if allowed_scope:
            valid = valid.intersection(set(allowed_scope.split()))

        # Filter to only valid scopes
        validated = requested.intersection(valid)

        if not validated:
            return 'odoo.read'

        return ' '.join(sorted(validated))

    @classmethod
    def has_scope(cls, token_scope, required_scope):
        """
        Check if token has required scope.

        :param token_scope: Space-separated scope string from token
        :param required_scope: Required scope string
        :return: True if scope is present
        """
        if not token_scope:
            return False

        token_scopes = set(token_scope.split())
        required_scopes = set(required_scope.split())

        return required_scopes.issubset(token_scopes)

    @classmethod
    def get_authorization_server_metadata(cls, base_url, path_db=None):
        """
        Generate OAuth 2.0 Authorization Server Metadata (RFC 8414).

        :param base_url: Base URL of the Odoo instance
        :param path_db: Optional database name for path-based multi-db support
        :return: Metadata dictionary
        """
        # Path-based URLs for multi-db: /<db>/oauth/...
        db_path = f"/{path_db}" if path_db else ""

        # Issuer is the base URL (optionally with db path for multi-db)
        # Note: Some clients may strip the path when constructing well-known URLs
        issuer = f"{base_url}{db_path}" if path_db else base_url

        return {
            "issuer": issuer,
            "authorization_endpoint": f"{base_url}{db_path}/oauth/authorize",
            "token_endpoint": f"{base_url}{db_path}/oauth/token",
            "registration_endpoint": f"{base_url}{db_path}/oauth/register",
            "revocation_endpoint": f"{base_url}{db_path}/oauth/revoke",
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": [
                "none",
                "client_secret_post",
                "client_secret_basic"
            ],
            "revocation_endpoint_auth_methods_supported": [
                "none",
                "client_secret_post",
                "client_secret_basic"
            ],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": cls.VALID_SCOPES,
            "service_documentation": f"{base_url}{db_path}/mcp/docs",
            # RFC 8707 Resource Indicators
            "resource_indicators_supported": True,
        }

    @classmethod
    def get_protected_resource_metadata(cls, base_url, path_db=None):
        """
        Generate OAuth 2.0 Protected Resource Metadata (RFC 9728).

        :param base_url: Base URL of the Odoo instance
        :param path_db: Optional database name for path-based multi-db support
        :return: Metadata dictionary
        """
        # Path-based URLs for multi-db: /<db>/mcp
        db_path = f"/{path_db}" if path_db else ""

        # Authorization server uses the same path prefix
        auth_server = f"{base_url}{db_path}" if path_db else base_url

        return {
            "resource": f"{base_url}",
            "authorization_servers": [auth_server],
            "scopes_supported": cls.VALID_SCOPES,
            "bearer_methods_supported": ["header"],
            "resource_signing_alg_values_supported": [],
        }

    @classmethod
    def parse_authorization_header(cls, auth_header):
        """
        Parse Authorization header for Bearer token.

        :param auth_header: Authorization header value
        :return: Token string or None
        """
        if not auth_header:
            return None

        parts = auth_header.split(' ', 1)
        if len(parts) != 2:
            return None

        scheme, token = parts
        if scheme.lower() != 'bearer':
            return None

        return token.strip()

    @classmethod
    def parse_basic_auth(cls, auth_header):
        """
        Parse Authorization header for Basic auth (client credentials).

        :param auth_header: Authorization header value
        :return: Tuple of (client_id, client_secret) or (None, None)
        """
        import base64

        if not auth_header:
            return None, None

        parts = auth_header.split(' ', 1)
        if len(parts) != 2:
            return None, None

        scheme, credentials = parts
        if scheme.lower() != 'basic':
            return None, None

        try:
            decoded = base64.b64decode(credentials).decode('utf-8')
            client_id, client_secret = decoded.split(':', 1)
            return client_id, client_secret
        except (ValueError, UnicodeDecodeError):
            return None, None

# -*- coding: utf-8 -*-
"""
Well-Known Endpoints Controller

Implements OAuth 2.0 discovery endpoints:
- RFC 8414: OAuth 2.0 Authorization Server Metadata
- RFC 9728: OAuth 2.0 Protected Resource Metadata
"""

import logging

from odoo import http
from odoo.http import request

from ..services.db_utils import get_db_from_request, get_base_url_or_host
from ..services.oauth_provider import OAuthProviderService

_logger = logging.getLogger(__name__)


class WellKnownController(http.Controller):
    """Controller for .well-known endpoints."""

    def _cors_headers(self):
        """Get CORS headers for responses."""
        return {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Authorization, Content-Type',
            'Access-Control-Max-Age': '86400',
        }

    @http.route(
        ['/.well-known/oauth-authorization-server',
         '/.well-known/oauth-authorization-server/<path:resource_path>'],
        type='http',
        auth='none',
        methods=['GET', 'OPTIONS'],
        csrf=False,
    )
    def oauth_authorization_server_metadata(self, resource_path=None, db=None, **kwargs):
        """
        OAuth 2.0 Authorization Server Metadata (RFC 8414).

        Returns metadata about this OAuth authorization server,
        including endpoints and supported features.

        Multi-database support:
        - resource_path: Database from URL path (e.g., /.well-known/.../dbname/mcp)
        - db: Database name from query parameter (fallback)
        """
        # Handle CORS preflight
        if request.httprequest.method == 'OPTIONS':
            return request.make_response('', headers=self._cors_headers())

        try:
            # Extract database from resource_path (e.g., "testmcp16/mcp" -> "testmcp16")
            # or use db query parameter as fallback
            path_db = resource_path.split('/')[0] if resource_path else db
            resolved_db, _ = get_db_from_request(required=False, path_db=path_db)
            base_url = get_base_url_or_host(resolved_db)

            metadata = OAuthProviderService.get_authorization_server_metadata(base_url, path_db=path_db)

            return request.make_json_response(metadata, headers={
                'Cache-Control': 'max-age=3600',
                **self._cors_headers(),
            })

        except Exception as e:
            _logger.exception("Error generating authorization server metadata")
            return request.make_json_response(
                {'error': 'server_error', 'error_description': str(e)},
                status=500,
                headers=self._cors_headers(),
            )

    @http.route(
        ['/.well-known/oauth-protected-resource',
         '/.well-known/oauth-protected-resource/<path:resource_path>'],
        type='http',
        auth='none',
        methods=['GET', 'OPTIONS'],
        csrf=False,
    )
    def oauth_protected_resource_metadata(self, resource_path=None, db=None, **kwargs):
        """
        OAuth 2.0 Protected Resource Metadata (RFC 9728).

        Returns metadata about the protected MCP resource,
        including the authorization servers that can issue tokens.

        Multi-database support:
        - resource_path: Database from URL path (e.g., /.well-known/.../dbname/mcp)
        - db: Database name from query parameter (fallback)
        """
        # Handle CORS preflight
        if request.httprequest.method == 'OPTIONS':
            return request.make_response('', headers=self._cors_headers())

        try:
            # Extract database from resource_path (e.g., "testmcp16/mcp" -> "testmcp16")
            # or use db query parameter as fallback
            path_db = resource_path.split('/')[0] if resource_path else db
            resolved_db, _ = get_db_from_request(required=False, path_db=path_db)
            base_url = get_base_url_or_host(resolved_db)

            metadata = OAuthProviderService.get_protected_resource_metadata(base_url, path_db=path_db)

            return request.make_json_response(metadata, headers={
                'Cache-Control': 'max-age=3600',
                **self._cors_headers(),
            })

        except Exception as e:
            _logger.exception("Error generating protected resource metadata")
            return request.make_json_response(
                {'error': 'server_error', 'error_description': str(e)},
                status=500,
                headers=self._cors_headers(),
            )

    @http.route(
        ['/.well-known/openid-configuration',
         '/.well-known/openid-configuration/<path:resource_path>'],
        type='http',
        auth='none',
        methods=['GET', 'OPTIONS'],
        csrf=False,
    )
    def openid_configuration(self, resource_path=None):
        """
        OpenID Connect Discovery (for compatibility).

        Some clients may look for this endpoint. We return the same
        as OAuth authorization server metadata.
        """
        # Handle CORS preflight
        if request.httprequest.method == 'OPTIONS':
            return request.make_response('', headers=self._cors_headers())

        # Extract database from resource_path (e.g., "testmcp16/mcp" -> "testmcp16")
        path_db = resource_path.split('/')[0] if resource_path else None
        db, _ = get_db_from_request(required=False, path_db=path_db)
        base_url = get_base_url_or_host(db)

        # Return the same as authorization server metadata
        metadata = OAuthProviderService.get_authorization_server_metadata(base_url, path_db=path_db)

        return request.make_json_response(metadata, headers={
            'Cache-Control': 'max-age=3600',
            **self._cors_headers(),
        })

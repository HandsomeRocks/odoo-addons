# -*- coding: utf-8 -*-
"""
OAuth 2.1 Provider Controller

Implements OAuth 2.1 authorization endpoints:
- /oauth/authorize - Authorization endpoint
- /oauth/token - Token endpoint
- /oauth/register - Dynamic Client Registration (RFC 7591)
- /oauth/revoke - Token revocation (RFC 7009)
"""

import json
import logging
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

from odoo import http
from odoo.http import request
from urllib.parse import quote

from ..services.db_utils import get_db_from_request, get_env
from ..services.oauth_provider import OAuthProviderService

_logger = logging.getLogger(__name__)


class OAuthController(http.Controller):
    """Controller for OAuth 2.1 endpoints."""

    def _cors_headers(self):
        """Get CORS headers for responses."""
        return {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Authorization, Content-Type',
            'Access-Control-Max-Age': '86400',
        }

    def _token_response_headers(self):
        """
        Get headers for token responses per RFC 6749 Section 5.1.

        Token responses MUST include Cache-Control: no-store and Pragma: no-cache
        to prevent caching of sensitive token data.
        """
        return {
            **self._cors_headers(),
            'Cache-Control': 'no-store',
            'Pragma': 'no-cache',
        }

    # -------------------------------------------------------------------------
    # Authorization Endpoint
    # -------------------------------------------------------------------------

    @http.route(
        '/<string:path_db>/oauth/authorize',
        type='http',
        auth='none',
        methods=['GET', 'POST'],
        csrf=False,
    )
    def authorize_multidb(self, path_db, response_type=None, client_id=None, redirect_uri=None,
                          scope=None, state=None, code_challenge=None,
                          code_challenge_method='S256', resource=None, action=None, **kwargs):
        """
        Multi-database OAuth Authorization Endpoint.

        Handles path-based database routing (/<db>/oauth/authorize).
        Validates database and session, then redirects to standard /oauth/authorize.
        """
        # Build OAuth params for redirects
        oauth_params = urlencode({
            k: v for k, v in {
                'response_type': response_type,
                'client_id': client_id,
                'redirect_uri': redirect_uri,
                'scope': scope,
                'state': state,
                'code_challenge': code_challenge,
                'code_challenge_method': code_challenge_method,
                'resource': resource,
                'action': action,
            }.items() if v
        })

        # Validate database exists
        db, db_error = get_db_from_request(required=True, path_db=path_db)
        if db_error:
            return request.make_json_response(db_error, status=400)

        # Check session state
        session_db = getattr(request.session, 'db', None)
        session_uid = getattr(request.session, 'uid', None)

        # Target URL for this multi-db path
        multidb_target = f'/{path_db}/oauth/authorize?{oauth_params}'

        if session_db != db or not session_uid:
            # User not logged in to this database - redirect to login
            login_url = f'/web/login?db={db}&redirect={quote(multidb_target, safe="")}'
            if session_db and session_db != db:
                # Logged into different db - logout first
                logout_url = f'/web/session/logout?redirect={quote(login_url, safe="")}'
                return request.redirect(logout_url, code=302, local=False)
            return request.redirect(login_url, code=302, local=False)

        # User is logged in to correct database - redirect to standard endpoint
        # The standard endpoint uses auth='user' so request.env will be properly set
        standard_url = f'/oauth/authorize?{oauth_params}'
        return request.redirect(standard_url, code=302, local=False)

    @http.route(
        '/oauth/authorize',
        type='http',
        auth='user',
        methods=['GET', 'POST'],
        csrf=False,
    )
    def authorize(self, response_type=None, client_id=None, redirect_uri=None,
                  scope=None, state=None, code_challenge=None,
                  code_challenge_method='S256', resource=None, action=None, **kwargs):
        """
        OAuth 2.1 Authorization Endpoint.

        GET: Show consent screen
        POST: User approves, generate code and redirect

        Required params (PKCE required for OAuth 2.1):
        - response_type: Must be 'code'
        - client_id: The client identifier
        - redirect_uri: Where to redirect after authorization
        - code_challenge: PKCE code challenge (Base64URL encoded)
        - code_challenge_method: Must be 'S256'

        Optional:
        - scope: Requested scopes (space-separated)
        - state: Opaque value for CSRF protection
        - resource: RFC 8707 resource indicator (MCP server URL for audience binding)
        """
        env = request.env

        # Check if MCP is enabled
        config = env['mcp.config'].sudo().get_config()
        if not config.enabled:
            return request.make_json_response(
                {'error': 'server_error', 'error_description': 'MCP server is disabled'},
                status=503
            )

        # Check if user has MCP User group
        user = env.user
        if not user.has_group('odoo_remote_mcp.group_mcp_user'):
            _logger.warning(
                "User %s (id=%s) attempted MCP authorization but lacks group_mcp_user",
                user.login, user.id
            )
            return request.make_json_response(
                {'error': 'access_denied',
                 'error_description': 'MCP access requires the MCP User group. Contact your administrator.'},
                status=403
            )

        # Validate required parameters
        if response_type != 'code':
            return self._authorization_error(
                redirect_uri, state, 'unsupported_response_type',
                'Only response_type=code is supported'
            )

        if not client_id:
            return self._authorization_error(
                redirect_uri, state, 'invalid_request',
                'client_id is required'
            )

        if not redirect_uri:
            return self._authorization_error(
                None, state, 'invalid_request',
                'redirect_uri is required'
            )

        # PKCE is required for OAuth 2.1
        if not code_challenge:
            return self._authorization_error(
                redirect_uri, state, 'invalid_request',
                'code_challenge is required (PKCE)'
            )

        if code_challenge_method != 'S256':
            return self._authorization_error(
                redirect_uri, state, 'invalid_request',
                'code_challenge_method must be S256'
            )

        # Find client
        Client = env['mcp.oauth.client'].sudo()
        client = Client.search([
            ('client_id', '=', client_id),
            ('active', '=', True),
        ], limit=1)

        if not client:
            return self._authorization_error(
                redirect_uri, state, 'invalid_client',
                'Client not found'
            )

        # Validate redirect URI
        if not client.is_redirect_uri_valid(redirect_uri):
            return self._authorization_error(
                None, state, 'invalid_request',
                f'Invalid redirect_uri for this client'
            )

        # Validate scope
        scope = OAuthProviderService.validate_scope(scope, client.scope)

        # Handle GET (show consent) vs POST (approve/deny)
        if request.httprequest.method == 'GET':
            return self._show_consent_screen(client, scope, redirect_uri, state,
                                             code_challenge, code_challenge_method, resource)
        else:
            # Check if user denied the request
            if action == 'deny':
                _logger.info("Authorization denied by user for client: %s", client.client_name)
                return self._authorization_error(
                    redirect_uri, state, 'access_denied',
                    'The user denied the authorization request'
                )
            return self._process_authorization(client, scope, redirect_uri, state,
                                               code_challenge, code_challenge_method, resource)

    def _show_consent_screen(self, client, scope, redirect_uri, state,
                             code_challenge, code_challenge_method, resource=None):
        """Render the OAuth consent screen."""
        scopes = scope.split() if scope else ['odoo.read']
        scope_descriptions = {
            'odoo.read': 'Read data from your Odoo instance',
            'odoo.write': 'Create and modify data in your Odoo instance',
            'odoo.execute': 'Execute code in your Odoo instance (advanced)',
            'offline_access': 'Stay connected with refresh tokens',
        }

        # Build switch user URL with properly encoded OAuth params
        oauth_params = urlencode({
            'response_type': 'code',
            'client_id': client.client_id,
            'redirect_uri': redirect_uri or '',
            'scope': scope or '',
            'state': state or '',
            'code_challenge': code_challenge or '',
            'code_challenge_method': code_challenge_method or '',
            'resource': resource or '',
        })
        switch_user_url = f'/web/session/logout?redirect={quote("/oauth/authorize?" + oauth_params, safe="")}'

        return request.render('odoo_remote_mcp.oauth_consent', {
            'client': client,
            'scopes': [(s, scope_descriptions.get(s, s)) for s in scopes],
            'redirect_uri': redirect_uri,
            'state': state or '',
            'code_challenge': code_challenge,
            'code_challenge_method': code_challenge_method,
            'scope': scope,
            'resource': resource or '',
            'switch_user_url': switch_user_url,
        })

    def _process_authorization(self, client, scope, redirect_uri, state,
                               code_challenge, code_challenge_method, resource=None):
        """Process authorization approval and redirect immediately."""
        user = request.env.user

        Code = request.env['mcp.oauth.code'].sudo()

        # Generate authorization code
        code = Code.generate_code(
            client=client,
            user=user,
            redirect_uri=redirect_uri,
            scope=scope,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            state=state,
            resource=resource,
        )

        # Build redirect URL with code
        params = {'code': code}
        if state:
            params['state'] = state

        redirect_url = self._add_params_to_url(redirect_uri, params)

        _logger.info("Authorization approved - Redirecting to: %s", redirect_url)

        # Immediate HTTP 302 redirect - critical for localhost callbacks that have short timeouts
        # (e.g., Claude Code's temporary callback server)
        return request.redirect(redirect_url, code=302, local=False)

    def _authorization_error(self, redirect_uri, state, error, description):
        """Return an authorization error response."""
        if redirect_uri:
            params = {
                'error': error,
                'error_description': description,
            }
            if state:
                params['state'] = state

            redirect_url = self._add_params_to_url(redirect_uri, params)
            return request.redirect(redirect_url, code=302, local=False)
        else:
            # Cannot redirect, show error page
            return request.make_json_response(
                {'error': error, 'error_description': description},
                status=400
            )

    def _add_params_to_url(self, url, params):
        """Add query parameters to a URL."""
        parsed = urlparse(url)
        existing_params = parse_qs(parsed.query)
        existing_params.update({k: [v] for k, v in params.items()})
        new_query = urlencode({k: v[0] for k, v in existing_params.items()})
        return urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, new_query, parsed.fragment
        ))

    # -------------------------------------------------------------------------
    # Token Endpoint
    # -------------------------------------------------------------------------

    @http.route(
        ['/oauth/token', '/token', '/<string:path_db>/oauth/token', '/<string:path_db>/token'],
        type='http',
        auth='none',
        methods=['POST', 'OPTIONS'],
        csrf=False,
        readonly=False,
    )
    def token(self, path_db=None, db=None, grant_type=None, **kwargs):
        """
        OAuth 2.1 Token Endpoint.

        Supports:
        - grant_type=authorization_code: Exchange code for tokens
        - grant_type=refresh_token: Refresh an access token

        For authorization_code:
        - code: The authorization code
        - redirect_uri: Must match the original request
        - client_id: The client identifier
        - code_verifier: PKCE code verifier

        For refresh_token:
        - refresh_token: The refresh token
        - client_id: The client identifier

        Multi-database support:
        - path_db: Database name from URL path (preferred)
        - db: Database name from query parameter (fallback)
        """
        # Handle CORS preflight
        if request.httprequest.method == 'OPTIONS':
            return request.make_response('', headers=self._cors_headers())

        # Resolve database - path_db takes priority, then query param db
        effective_path_db = path_db or db
        db, db_error = get_db_from_request(required=True, path_db=effective_path_db)
        if db_error:
            return request.make_json_response(
                db_error,
                status=400,
                headers=self._cors_headers(),
            )

        with get_env(db) as env:
            return self._process_token_request(env, grant_type, **kwargs)

    def _process_token_request(self, env, grant_type, **kwargs):
        """Process token request with given environment."""
        # Check if MCP is enabled
        config = env['mcp.config'].sudo().get_config()
        if not config.enabled:
            return request.make_json_response(
                {'error': 'server_error', 'error_description': 'MCP server is disabled'},
                status=503,
                headers=self._cors_headers(),
            )

        if grant_type == 'authorization_code':
            return self._token_authorization_code(env, **kwargs)
        elif grant_type == 'refresh_token':
            return self._token_refresh(env, **kwargs)
        else:
            return request.make_json_response(
                {'error': 'unsupported_grant_type',
                 'error_description': 'Supported: authorization_code, refresh_token'},
                status=400,
                headers=self._cors_headers(),
            )

    def _token_authorization_code(self, env, code=None, redirect_uri=None,
                                   client_id=None, code_verifier=None, resource=None, **kwargs):
        """Handle authorization_code grant."""
        _logger.info("Token request - client_id: %s, redirect_uri: %s, code: %s..., verifier: %s..., resource: %s",
                     client_id, redirect_uri, code[:20] if code else None, code_verifier[:20] if code_verifier else None, resource)

        if not all([code, redirect_uri, client_id, code_verifier]):
            _logger.warning("Token request - Missing parameters: code=%s, redirect_uri=%s, client_id=%s, code_verifier=%s",
                           bool(code), bool(redirect_uri), bool(client_id), bool(code_verifier))
            return request.make_json_response(
                {'error': 'invalid_request',
                 'error_description': 'Missing required parameters'},
                status=400,
                headers=self._cors_headers(),
            )

        # Find client
        Client = env['mcp.oauth.client'].sudo()
        client = Client.search([
            ('client_id', '=', client_id),
            ('active', '=', True),
        ], limit=1)

        if not client:
            _logger.warning("Token request - Client not found: %s", client_id)
            return request.make_json_response(
                {'error': 'invalid_client', 'error_description': 'Client not found'},
                status=401,
                headers=self._cors_headers(),
            )

        _logger.info("Token request - Found client: %s (type: %s)", client.client_name, client.client_type)

        # Authenticate confidential clients
        if client.client_type == 'confidential':
            if not self._authenticate_client(client, kwargs):
                _logger.warning("Token request - Client auth failed for: %s", client_id)
                return request.make_json_response(
                    {'error': 'invalid_client',
                     'error_description': 'Client authentication failed'},
                    status=401,
                    headers=self._cors_headers(),
                )

        # Exchange code for tokens
        Code = env['mcp.oauth.code'].sudo()
        tokens = Code.exchange_code(code, code_verifier, client, redirect_uri, resource)

        if not tokens:
            _logger.warning("Token request - Code exchange failed for code: %s...", code[:20] if code else None)
            return request.make_json_response(
                {'error': 'invalid_grant',
                 'error_description': 'Invalid or expired authorization code'},
                status=400,
                headers=self._cors_headers(),
            )

        _logger.info("Token request - Success! Issued tokens with scope: %s", tokens.get('scope'))
        return request.make_json_response(tokens, headers=self._token_response_headers())

    def _token_refresh(self, env, refresh_token=None, client_id=None, **kwargs):
        """Handle refresh_token grant."""
        if not refresh_token or not client_id:
            return request.make_json_response(
                {'error': 'invalid_request',
                 'error_description': 'Missing refresh_token or client_id'},
                status=400,
                headers=self._cors_headers(),
            )

        # Find client
        Client = env['mcp.oauth.client'].sudo()
        client = Client.search([
            ('client_id', '=', client_id),
            ('active', '=', True),
        ], limit=1)

        if not client:
            return request.make_json_response(
                {'error': 'invalid_client', 'error_description': 'Client not found'},
                status=401,
                headers=self._cors_headers(),
            )

        # Authenticate confidential clients
        if client.client_type == 'confidential':
            if not self._authenticate_client(client, kwargs):
                return request.make_json_response(
                    {'error': 'invalid_client',
                     'error_description': 'Client authentication failed'},
                    status=401,
                    headers=self._cors_headers(),
                )

        # Refresh tokens
        Token = env['mcp.oauth.token'].sudo()
        tokens = Token.refresh_tokens(refresh_token, client)

        if not tokens:
            return request.make_json_response(
                {'error': 'invalid_grant',
                 'error_description': 'Invalid or expired refresh token'},
                status=400,
                headers=self._cors_headers(),
            )

        return request.make_json_response(tokens, headers=self._token_response_headers())

    def _authenticate_client(self, client, kwargs):
        """Authenticate a confidential client."""
        # Try Basic auth first
        auth_header = request.httprequest.headers.get('Authorization', '')
        basic_client_id, basic_secret = OAuthProviderService.parse_basic_auth(auth_header)

        if basic_client_id:
            if basic_client_id != client.client_id:
                return False
            return client.verify_secret(basic_secret)

        # Try client_secret_post
        client_secret = kwargs.get('client_secret')
        if client_secret:
            return client.verify_secret(client_secret)

        return False

    # -------------------------------------------------------------------------
    # Dynamic Client Registration (RFC 7591)
    # -------------------------------------------------------------------------

    @http.route(
        ['/oauth/register', '/register', '/<string:path_db>/oauth/register', '/<string:path_db>/register'],
        type='http',
        auth='none',
        methods=['POST', 'GET', 'OPTIONS'],
        csrf=False,
    )
    def register(self, path_db=None, db=None, **kwargs):
        """
        Dynamic Client Registration (RFC 7591).

        Allows clients like Claude to register themselves.

        Request body (JSON):
        {
            "redirect_uris": ["https://..."],
            "client_name": "Claude",
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "none"
        }

        Multi-database support:
        - path_db: Database name from URL path (preferred)
        - db: Database name from query parameter (fallback)
        """
        # Handle CORS preflight
        if request.httprequest.method == 'OPTIONS':
            return request.make_response('', headers=self._cors_headers())

        # Handle GET requests - return method info (some clients probe endpoints)
        if request.httprequest.method == 'GET':
            return request.make_json_response(
                {
                    'error': 'invalid_request',
                    'error_description': 'Dynamic Client Registration requires POST method with JSON body',
                    'supported_methods': ['POST'],
                },
                status=405,
                headers={
                    'Allow': 'POST, OPTIONS',
                    **self._cors_headers(),
                }
            )

        # Resolve database - path_db takes priority, then query param db
        effective_path_db = path_db or db
        db, db_error = get_db_from_request(required=True, path_db=effective_path_db)
        if db_error:
            return request.make_json_response(db_error, status=400, headers=self._cors_headers())

        with get_env(db) as env:
            return self._process_register_request(env)

    def _process_register_request(self, env):
        """Process client registration request with given environment."""
        # Check if MCP is enabled
        config = env['mcp.config'].sudo().get_config()
        if not config.enabled:
            return request.make_json_response(
                {'error': 'server_error', 'error_description': 'MCP server is disabled'},
                status=503
            )

        # Parse request body
        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return request.make_json_response(
                {'error': 'invalid_request',
                 'error_description': 'Invalid JSON body'},
                status=400
            )

        # Register client
        try:
            Client = env['mcp.oauth.client'].sudo()
            response = Client.register_client(data)
            return request.make_json_response(response, status=201)

        except ValueError as e:
            return request.make_json_response(
                {'error': 'invalid_request', 'error_description': str(e)},
                status=400
            )
        except Exception as e:
            _logger.exception("Error registering OAuth client")
            return request.make_json_response(
                {'error': 'server_error', 'error_description': str(e)},
                status=500
            )

    # -------------------------------------------------------------------------
    # Token Revocation (RFC 7009)
    # -------------------------------------------------------------------------

    @http.route(
        ['/oauth/revoke', '/<string:path_db>/oauth/revoke'],
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
    )
    def revoke(self, path_db=None, db=None, token=None, token_type_hint=None, **kwargs):
        """
        Token Revocation (RFC 7009).

        Revokes an access or refresh token.

        Parameters:
        - token: The token to revoke
        - token_type_hint: Optional hint (access_token or refresh_token)

        Multi-database support:
        - path_db: Database name from URL path (preferred)
        - db: Database name from query parameter (fallback)
        """
        if not token:
            return request.make_json_response(
                {'error': 'invalid_request', 'error_description': 'token is required'},
                status=400
            )

        # Resolve database - path_db takes priority, then query param db
        effective_path_db = path_db or db
        db, db_error = get_db_from_request(required=True, path_db=effective_path_db)
        if db_error:
            return request.make_json_response(db_error, status=400)

        with get_env(db) as env:
            Token = env['mcp.oauth.token'].sudo()
            Token.revoke_token(token, token_type_hint)

        # Always return 200 per RFC 7009 (even if token didn't exist)
        return request.make_json_response({})

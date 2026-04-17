# -*- coding: utf-8 -*-
"""
MCP Protocol Controller

Implements the Model Context Protocol (MCP) endpoint using
Streamable HTTP transport at /mcp.

Protocol version: 2025-11-25
Spec: https://modelcontextprotocol.io/specification/2025-11-25/
"""

import json
import logging
import base64

from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError, UserError

from ..services.binary_utils import attachment_to_resource_content, fetch_field_resource_content
from ..services.db_utils import get_db_from_request, get_env, get_base_url, has_request_env, with_db_env
from ..services.oauth_provider import OAuthProviderService
from ..services.protocol import (
    MCPProtocolHandler, MCPError, MCP_PROTOCOL_VERSION
)
from ..services.tools import MCPToolRegistry

_logger = logging.getLogger(__name__)


class MCPController(http.Controller):
    """
    Controller for MCP protocol endpoint.

    Implements the Streamable HTTP transport as defined in MCP 2025-11-25 spec.
    """

    @http.route(
        ['/mcp', '/<string:path_db>/mcp'],
        type='http',
        auth='none',
        methods=['POST', 'GET', 'OPTIONS'],
        csrf=False,
        readonly=False,  # auth='none' defaults to readonly=True, but MCP needs RW for write operations
    )
    def mcp_endpoint(self, path_db=None, db=None, **kwargs):
        """
        MCP Streamable HTTP transport endpoint.

        Per MCP spec, this single endpoint supports:
        - POST: Send JSON-RPC messages (requests, notifications, responses)
        - GET: Server info
        - OPTIONS: CORS preflight

        Security:
        - Validates OAuth bearer token
        - Returns 401 with WWW-Authenticate header if no/invalid token

        Multi-database support:
        - path_db: Database name from URL path (e.g., /<db>/mcp) - preferred
        - db: Database name from query parameter (e.g., /mcp?db=mydb) - fallback
        """

        # Handle CORS preflight
        if request.httprequest.method == 'OPTIONS':
            return self._cors_response()

        # Resolve database - path_db takes priority, then query param db
        effective_path_db = path_db or db
        db, db_error = get_db_from_request(required=True, path_db=effective_path_db)
        if db_error:
            return request.make_json_response(
                db_error,
                status=400,
                headers=self._cors_headers()
            )

        # Handle GET for server info
        if request.httprequest.method == 'GET':
            return self._handle_get(db, effective_path_db)

        # Parse MCP message first (doesn't need database context)
        raw_data = request.httprequest.data.decode('utf-8').strip()

        # Handle empty body probe request - return 401 to trigger OAuth discovery
        # Per MCP spec, clients may send empty request to discover auth requirements
        if not raw_data:
            _logger.info("MCP Request - Empty body probe, returning 401 for OAuth discovery")
            return self._unauthorized_response(db=db, path_db=effective_path_db)

        try:
            body = json.loads(raw_data)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            _logger.error("MCP Request - Parse error: %s, raw data: %s", e, request.httprequest.data[:200])
            return self._jsonrpc_error(None, MCPProtocolHandler.PARSE_ERROR,
                                       f"Parse error: {e}")

        # Handle empty JSON object {} probe - also return 401 for OAuth discovery
        if body == {}:
            _logger.info("MCP Request - Empty JSON object probe, returning 401 for OAuth discovery")
            return self._unauthorized_response(db=db, path_db=effective_path_db)

        # Check for batch requests - not allowed for initialize per spec
        if isinstance(body, list):
            for msg in body:
                if isinstance(msg, dict) and msg.get('method') == 'initialize':
                    return self._jsonrpc_error(
                        msg.get('id'),
                        MCPProtocolHandler.INVALID_REQUEST,
                        "Initialize request MUST NOT be part of a JSON-RPC batch"
                    )

        # Handle the message
        try:
            method, params, msg_id = MCPProtocolHandler.parse_message(body)
        except MCPError as e:
            return self._jsonrpc_error(None, e.code, e.message, e.data)

        # All methods require authentication
        auth_header = request.httprequest.headers.get('Authorization', '')
        token = OAuthProviderService.parse_authorization_header(auth_header)

        if not token:
            # No token provided - client needs to authenticate
            return self._unauthorized_response(error=None, db=db, path_db=effective_path_db)

        # Use request.env if available (preserves Odoo request lifecycle and hooks)
        # Fall back to with_db_env only when request.env is not set
        if has_request_env():
            return self._process_mcp_request(request.env, method, params, msg_id, token, db, effective_path_db)
        else:
            with with_db_env(db) as env:
                return self._process_mcp_request(env, method, params, msg_id, token, db, effective_path_db)

    def _process_mcp_request(self, env, method, params, msg_id, token, db, path_db=None):
        """
        Process an authenticated MCP request.

        :param env: Odoo environment
        :param method: MCP method name
        :param params: Method parameters
        :param msg_id: JSON-RPC message ID
        :param token: Bearer token string
        :param db: Database name for error responses
        :param path_db: Database from URL path (for multi-db)
        """
        # Check if MCP is enabled
        config = env['mcp.config'].sudo().get_config()
        if not config.enabled:
            return request.make_json_response(
                {'error': 'service_unavailable', 'message': 'MCP server is disabled'},
                status=503,
                headers=self._cors_headers()
            )

        # Validate token - try OAuth first, then API key fallback
        Token = env['mcp.oauth.token'].sudo()
        token_data = Token.validate_access_token(token)

        if not token_data:
            token_data = env['mcp.api.key'].validate_api_key(token)

        if not token_data:
            # Token provided but invalid/expired - client should try refresh
            return self._unauthorized_response(error='invalid_token', db=db, path_db=path_db)

        # Set up environment with authenticated user
        user = env['res.users'].sudo().browse(token_data['user_id'])
        if not user.exists() or not user.active or not user.has_group('odoo_remote_mcp.group_mcp_user'):
            return self._unauthorized_response(error='invalid_token', db=db, path_db=path_db)

        # Switch to authenticated user context
        # Use update_env when request.env is available (preserves hooks like message tracking)
        if has_request_env():
            request.update_env(user=user.id)
            env = request.env
        else:
            env = env(user=user.id)

        # Dispatch to handler
        try:
            result = self._dispatch_method(env, method, params, token_data)

            # Notifications don't get responses - return 202 Accepted
            if result is None and msg_id is None:
                return request.make_response(
                    '',
                    status=202,
                    headers=list(self._cors_headers().items())
                )

            response = MCPProtocolHandler.format_response(msg_id, result)
            return request.make_json_response(response, headers=self._cors_headers())

        except MCPError as e:
            return self._jsonrpc_error(msg_id, e.code, e.message, e.data)
        except AccessError as e:
            return self._jsonrpc_error(msg_id, MCPProtocolHandler.FORBIDDEN,
                                       f"Access denied: {e}")
        except (ValidationError, UserError) as e:
            return self._jsonrpc_error(msg_id, MCPProtocolHandler.INVALID_PARAMS,
                                       str(e))
        except Exception as e:
            _logger.exception("MCP internal error")
            return self._jsonrpc_error(msg_id, MCPProtocolHandler.INTERNAL_ERROR,
                                       str(e))

    def _handle_get(self, db=None, path_db=None):
        """
        Handle GET request - return server info.

        :param db: Database name
        :param path_db: Database from URL path (for multi-db)
        """
        with get_env(db) as env:
            config = env['mcp.config'].sudo().get_config()
            base_url = get_base_url(env)
            server_name = config.server_name or 'Odoo MCP Server'
            enabled = config.enabled

        # Per MCP spec: well-known paths use suffix format
        db_path = f"/{path_db}" if path_db else ""
        wellknown_suffix = f"/{path_db}/mcp" if path_db else ""

        return request.make_json_response({
            'name': server_name,
            'protocol': 'mcp',
            'version': MCP_PROTOCOL_VERSION,
            'status': 'running' if enabled else 'disabled',
            'documentation': f'{base_url}{db_path}/mcp/docs',
            'oauth': {
                'authorization_server': f'{base_url}/.well-known/oauth-authorization-server{wellknown_suffix}',
                'protected_resource': f'{base_url}/.well-known/oauth-protected-resource{wellknown_suffix}',
            }
        }, headers=self._cors_headers())

    def _dispatch_method(self, env, method, params, token_data):
        """
        Dispatch an MCP method to its handler.

        :param env: Odoo environment
        :param method: MCP method name
        :param params: Method parameters
        :param token_data: Validated token data
        :return: Result from handler
        """
        # Map methods to handlers
        handlers = {
            'initialize': self._handle_initialize,
            'ping': self._handle_ping,
            'notifications/initialized': self._handle_initialized,
            'tools/list': self._handle_tools_list,
            'tools/call': self._handle_tools_call,
            'resources/list': self._handle_resources_list,
            'resources/templates/list': self._handle_resources_templates_list,
            'resources/read': self._handle_resources_read,
            'prompts/list': self._handle_prompts_list,
            'prompts/get': self._handle_prompts_get,
            #'completion/complete': self._handle_completion_complete, # could todo in future, provide completions for prompts/resource attachment names
        }

        handler = handlers.get(method)
        if not handler:
            raise MCPError(
                MCPProtocolHandler.METHOD_NOT_FOUND,
                f"Method not found: {method}"
            )

        return handler(env, params, token_data)

    # -------------------------------------------------------------------------
    # MCP Method Handlers
    # -------------------------------------------------------------------------

    def _handle_initialize(self, env, params, token_data):
        """
        Handle initialize request.

        Per MCP spec, this MUST be the first request and validates:
        - protocolVersion (required)
        - capabilities (required)
        - clientInfo with name and version (required)
        """
        return MCPProtocolHandler.handle_initialize(env, params)

    def _handle_ping(self, env, params, token_data):
        """Handle ping request."""
        return MCPProtocolHandler.handle_ping(env, params)

    def _handle_initialized(self, env, params, token_data):
        """
        Handle initialized notification.

        Per MCP spec, client MUST send this after successful initialize.
        """
        return MCPProtocolHandler.handle_notifications_initialized(env, params)

    def _handle_tools_list(self, env, params, token_data):
        """Handle tools/list request."""
        scope = token_data.get('scope', '')
        tools = MCPToolRegistry.get_tools_list(env, scope)
        return {'tools': tools}

    def _handle_tools_call(self, env, params, token_data):
        """
        Handle tools/call request.

        Per MCP spec, tool execution errors are returned as successful
        responses with isError=true, not as JSON-RPC errors.
        """
        tool_name = params.get('name')
        arguments = params.get('arguments', {})

        if not tool_name:
            raise MCPError(
                MCPProtocolHandler.INVALID_PARAMS,
                "Tool name is required"
            )

        # Get IP address for logging
        ip_address = request.httprequest.remote_addr

        # Execute tool - returns MCP format with content and optional isError
        Log = env['mcp.execution.log'].sudo()
        return MCPToolRegistry.call_tool(
            env, tool_name, arguments, token_data, Log, ip_address
        )

    def _handle_resources_list(self, env, params, token_data):
        """
        Handle resources/list request.

        Returns empty list - use resources/templates/list for template discovery,
        or use search_read on ir.attachment and resources/read with odoo://attachment/{id}.
        """
        return {'resources': []}

    def _handle_resources_templates_list(self, env, params, token_data):
        """
        Handle resources/templates/list request.

        Returns resource templates per MCP spec for clients that support it.
        """
        templates = []

        templates.append({
            'uriTemplate': 'odoo://attachments{?ids}',
            'name': 'Odoo Attachments',
            'description': (
                f'Read attachments by ID. Pass comma-separated IDs'
                f'Example: odoo://attachments?ids=1,2,3'
            ),
            'mimeType': 'application/octet-stream',
        })

        # Record binary field template (model check at read time)
        templates.append({
            'uriTemplate': 'odoo://record/{model}/{field}{?ids}',
            'name': 'Record Binary Field',
            'description': (
                f'Read binary/image field from records'
                f'Example: odoo://record/res.partner/image_128?ids=1,2,3'
            ),
            'mimeType': 'application/octet-stream',
        })

        return {'resourceTemplates': templates}

    def _handle_resources_read(self, env, params, token_data):
        """
        Handle resources/read request.

        Supports URI patterns:
        - odoo://attachments?ids=1,2,3 - Read attachments by IDs
        - odoo://record/{model}/{field}?ids=1,2,3 - Read binary field from records

        Returns text content for text mimetypes, base64 blob for binary.
        Enforces model/field restrictions from MCP config.
        """
        uri = params.get('uri')

        if not uri:
            raise MCPError(
                MCPProtocolHandler.INVALID_PARAMS,
                "Resource URI is required"
            )

        # Get config and restrictions
        config = env['mcp.config'].sudo().get_config()
        restricted_models = config.get_restricted_models_for_user(env.user)
        restricted_fields = config.get_restricted_fields_for_user(env.user)

        # Route to appropriate handler based on URI pattern
        if uri.startswith('odoo://attachments?ids='):
            return self._read_attachments(env, uri, restricted_models, restricted_fields)

        elif uri.startswith('odoo://record/') and '?ids=' in uri:
            return self._read_record_binary(env, uri, restricted_models, restricted_fields)

        else:
            raise MCPError(
                MCPProtocolHandler.INVALID_PARAMS,
                f"Invalid resource URI format: {uri}. "
                f"Expected: odoo://attachments?ids=... or odoo://record/{{model}}/{{field}}?ids=..."
            )

    def _read_attachments(self, env, uri, restricted_models, restricted_fields):
        """Read attachments by IDs."""
        # Check access - block if ir.attachment has read restricted
        if restricted_models is not None and 'ir.attachment' in restricted_models:
            if restricted_models['ir.attachment'].get('read', True):
                raise MCPError(
                    MCPProtocolHandler.FORBIDDEN,
                    "Access to ir.attachment is restricted"
                )
        if restricted_fields and ('ir.attachment', 'datas') in restricted_fields:
            raise MCPError(
                MCPProtocolHandler.FORBIDDEN,
                "Access to attachment content is restricted"
            )

        # Parse IDs from URI: odoo://attachments?ids=1,2,3
        ids_str = uri.replace('odoo://attachments?ids=', '')
        try:
            ids = [int(x.strip()) for x in ids_str.split(',') if x.strip()]
        except ValueError:
            raise MCPError(
                MCPProtocolHandler.INVALID_PARAMS,
                "Invalid attachment IDs in URI"
            )

        if not ids:
            raise MCPError(
                MCPProtocolHandler.INVALID_PARAMS,
                "No attachment IDs provided"
            )

        attachments = env['ir.attachment'].browse(ids).exists()
        contents = []
        for att in attachments:
            att_uri = f'odoo://attachments?ids={att.id}'
            contents.append(attachment_to_resource_content(att, att_uri))

        return {'contents': contents}

    def _read_record_binary(self, env, uri, restricted_models, restricted_fields):
        """Read binary field from records: odoo://record/{model}/{field}?ids=1,2,3"""
        # Parse URI
        base_path, ids_str = uri.split('?ids=')
        parts = base_path.replace('odoo://record/', '').split('/')
        if len(parts) != 2:
            raise MCPError(
                MCPProtocolHandler.INVALID_PARAMS,
                "Invalid URI format. Expected: odoo://record/{model}/{field}?ids=..."
            )

        model_name, field_name = parts

        try:
            ids = [int(x.strip()) for x in ids_str.split(',') if x.strip()]
        except ValueError:
            raise MCPError(
                MCPProtocolHandler.INVALID_PARAMS,
                "Invalid record IDs in URI"
            )

        if not ids:
            raise MCPError(
                MCPProtocolHandler.INVALID_PARAMS,
                "No record IDs provided"
            )

        # Check model restrictions - block if model has read restricted
        if restricted_models is not None and model_name in restricted_models:
            if restricted_models[model_name].get('read', True):
                raise MCPError(
                    MCPProtocolHandler.FORBIDDEN,
                    f"Access to {model_name} is restricted"
                )

        # Check field restrictions
        if restricted_fields and (model_name, field_name) in restricted_fields:
            raise MCPError(
                MCPProtocolHandler.FORBIDDEN,
                f"Access to {model_name}.{field_name} is restricted"
            )

        # Validate model exists
        if model_name not in env:
            raise MCPError(
                MCPProtocolHandler.NOT_FOUND,
                f"Model not found: {model_name}"
            )

        Model = env[model_name]
        field = Model._fields.get(field_name)

        # Validate field is binary
        if not field or field.type not in ('binary', 'image'):
            raise MCPError(
                MCPProtocolHandler.INVALID_PARAMS,
                f"Field {field_name} is not a binary field on {model_name}"
            )

        # Verify records exist
        existing_ids = Model.browse(ids).exists().ids
        contents = []

        for record_id in existing_ids:
            record_uri = f'odoo://record/{model_name}/{field_name}?ids={record_id}'

            # Use centralized fetch which handles attachment-backed fields,
            # URL types, and text/binary detection
            content = fetch_field_resource_content(
                env, model_name, field_name, record_id, record_uri
            )

            if content:
                contents.append(content)

        return {'contents': contents}

    def _handle_prompts_list(self, env, params, token_data):
        """
        Handle prompts/list request.

        Returns prompts that are:
        - Active and exposed to MCP clients
        - Visible to the current user (owned by user, shared with all, or explicitly shared)

        Per MCP spec: https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
        """
        Prompt = env['mcp.prompt'].sudo()
        prompts = Prompt.get_prompts_for_mcp(user=env.user)

        return {'prompts': [p.get_mcp_format() for p in prompts]}

    def _handle_prompts_get(self, env, params, token_data):
        """
        Handle prompts/get request.

        Fetches a specific prompt by name and substitutes arguments.
        Only returns prompts visible to the current user.
        Per MCP spec: https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
        """
        name = params.get('name')
        arguments = params.get('arguments', {})

        if not name:
            raise MCPError(
                MCPProtocolHandler.INVALID_PARAMS,
                "Prompt name is required"
            )

        Prompt = env['mcp.prompt'].sudo()
        # Build domain including visibility filter
        domain = [
            ('name', '=', name),
            ('active', '=', True),
            ('expose_to_mcp_client', '=', True),
        ] + Prompt._get_visible_domain(env.user)

        prompt = Prompt.search(domain, limit=1)

        if not prompt:
            raise MCPError(
                MCPProtocolHandler.NOT_FOUND,
                f"Prompt not found: {name}"
            )

        return prompt.get_prompt_message(arguments)

    def _handle_completion_complete(self, env, params, token_data):
        """
        Handle completion/complete request.

        Supports completions for:
        - ref/prompt: Prompt argument completions
        - ref/resource: Resource template argument completions
        """
        ref = params.get('ref')
        argument = params.get('argument')

        if not ref or not argument:
            raise MCPError(
                MCPProtocolHandler.INVALID_PARAMS,
                "Missing required parameters: ref, argument"
            )

        ref_type = ref.get('type')

        if ref_type == 'ref/prompt':
            # todo could implement in future
            return {'completion': {'values': [], 'total': 0, 'hasMore': False}}

        elif ref_type == 'ref/resource':
            # Resource template completions
            # uri_template = ref.get('uri')
            return {'completion': {'values': [], 'total': 0, 'hasMore': False}}

        else:
            return {'completion': {'values': [], 'total': 0, 'hasMore': False}}

    # -------------------------------------------------------------------------
    # Response Helpers
    # -------------------------------------------------------------------------

    def _unauthorized_response(self, error=None, db=None, path_db=None):
        """
        Return 401 with WWW-Authenticate header per RFC 6750 and RFC 9728.

        Per MCP spec, the WWW-Authenticate header MUST include:
        - resource_metadata: URL to OAuth Protected Resource Metadata
        - scope: The scopes required for this resource (SHOULD be included)

        :param error: OAuth error code - 'invalid_token' if token was provided but
                      expired/invalid (signals client to try refresh), None if no
                      token was provided (signals client to authenticate).
        :param db: Database name
        :param path_db: Database from URL path (for multi-db)
        """
        # Get base_url
        if db:
            with get_env(db) as env:
                base_url = get_base_url(env)
        else:
            # Fallback: use request host URL when no db available
            base_url = request.httprequest.host_url.rstrip('/')

        scopes = ' '.join(OAuthProviderService.VALID_SCOPES)

        # Per MCP spec: /.well-known/oauth-protected-resource/<path>
        # where <path> matches the MCP endpoint path (e.g., /testmcp16/mcp)
        if path_db:
            resource_metadata_url = f"{base_url}/.well-known/oauth-protected-resource/{path_db}/mcp"
        else:
            resource_metadata_url = f"{base_url}/.well-known/oauth-protected-resource"

        # Build WWW-Authenticate header per RFC 6750 Section 3 and RFC 9728
        www_auth_parts = [
            f'resource_metadata="{resource_metadata_url}"',
            f'scope="{scopes}"',
        ]

        # Build response body per RFC 6750 Section 3.1
        # Error codes: invalid_request, invalid_token, insufficient_scope
        if error == 'invalid_token':
            # Token was provided but expired/invalid - client should try refresh
            www_auth_parts.append('error="invalid_token"')
            www_auth_parts.append('error_description="The access token has expired"')
            response_body = {
                'error': 'invalid_token',
                'error_description': 'The access token has expired',
            }
        else:
            # No token provided - client needs to authenticate
            response_body = {
                'error': 'invalid_request',
                'error_description': 'No access token provided',
            }

        headers = {
            'WWW-Authenticate': f'Bearer {", ".join(www_auth_parts)}',
            **self._cors_headers()
        }

        return request.make_json_response(
            response_body,
            status=401,
            headers=headers
        )

    def _jsonrpc_error(self, msg_id, code, message, data=None):
        """Return a JSON-RPC error response."""
        response = MCPProtocolHandler.format_error(msg_id, code, message, data)
        return request.make_json_response(response, headers=self._cors_headers())

    def _cors_headers(self):
        """Get CORS headers for responses."""
        return {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Authorization, Content-Type',
            'Access-Control-Max-Age': '86400',
        }

    def _cors_response(self):
        """Return a CORS preflight response."""
        return request.make_response(
            '',
            headers=list(self._cors_headers().items()),
            status=204
        )


class MCPDocsController(http.Controller):
    """Controller for MCP documentation."""

    @http.route(['/mcp/docs', '/<string:path_db>/mcp/docs'], type='http', auth='none', methods=['GET'], csrf=False)
    def docs(self, path_db=None):
        """Serve MCP documentation page."""
        # Resolve database - path_db takes priority
        db, db_error = get_db_from_request(required=True, path_db=path_db)
        if db_error:
            return request.make_json_response(db_error, status=400)

        # Get config data
        with get_env(db) as env:
            config = env['mcp.config'].sudo().get_config()
            base_url = get_base_url(env)
            server_name = config.server_name or 'Odoo MCP Server'

        # request.render requires request.env - return JSON fallback otherwise
        if has_request_env():
            return request.render('odoo_remote_mcp.mcp_docs', {
                'base_url': base_url,
                'server_name': server_name,
                'db': db,
            })
        else:
            return request.make_json_response({
                'message': 'MCP Documentation',
                'server_name': server_name,
                'base_url': base_url,
                'hint': 'Access this page with a valid session for the full HTML documentation.',
            })

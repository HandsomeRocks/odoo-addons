# -*- coding: utf-8 -*-
"""
MCP Protocol Handler Service

Implements the Model Context Protocol (version 2025-11-25).
Handles message parsing, routing, and response formatting.

Spec: https://modelcontextprotocol.io/specification/2025-11-25/
"""

import logging

_logger = logging.getLogger(__name__)

# MCP Protocol Version - the version this server implements
MCP_PROTOCOL_VERSION = '2025-11-25'

# Supported protocol versions for backwards compatibility
SUPPORTED_PROTOCOL_VERSIONS = ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05']


class MCPError(Exception):
    """Base exception for MCP protocol errors."""

    def __init__(self, code, message, data=None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


class MCPProtocolHandler:
    """
    MCP Protocol Handler.

    Handles JSON-RPC 2.0 message format used by MCP.
    Implements the MCP 2025-11-25 specification.
    """

    # Standard JSON-RPC error codes
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # MCP-specific error codes
    UNAUTHORIZED = -32001
    FORBIDDEN = -32002
    NOT_FOUND = -32003

    @classmethod
    def parse_message(cls, data):
        """
        Parse and validate a JSON-RPC 2.0 message.

        Per MCP spec, messages must follow JSON-RPC 2.0 format.

        :param data: Parsed JSON dictionary
        :return: Tuple of (method, params, id)
        :raises MCPError: If message is invalid
        """
        if not isinstance(data, dict):
            raise MCPError(cls.INVALID_REQUEST, "Request must be a JSON object")

        # Check JSON-RPC version
        jsonrpc = data.get('jsonrpc')
        if jsonrpc != '2.0':
            raise MCPError(cls.INVALID_REQUEST, "JSON-RPC version must be '2.0'")

        method = data.get('method')
        if not method or not isinstance(method, str):
            raise MCPError(cls.INVALID_REQUEST, "Method must be a non-empty string")

        params = data.get('params', {})
        if not isinstance(params, dict):
            raise MCPError(cls.INVALID_PARAMS, "Params must be an object")

        msg_id = data.get('id')

        return method, params, msg_id

    @classmethod
    def validate_protocol_version(cls, version):
        """
        Validate and negotiate protocol version.

        Per MCP spec:
        - Client sends protocolVersion in initialize request
        - Server responds with same version if supported, or alternative

        For forward compatibility, unknown versions are accepted since
        MCP is designed to be backwards compatible.

        :param version: Client's requested protocol version
        :return: Negotiated protocol version
        """

        # TODO IF FUTURE VERSIONS OF MCP PROTOCOL BREAK THE MODULE, UPDATE TO LAST SUPPORTED NEGOTIATED PROTOCOL VERSION
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            _logger.warning(
                "Client requested unknown protocol version '%s'. "
                "Accepting anyway for forward compatibility. "
                "Known versions: %s",
                version,
                SUPPORTED_PROTOCOL_VERSIONS
            )
        return version

    @classmethod
    def format_response(cls, msg_id, result):
        """
        Format a successful JSON-RPC response.

        :param msg_id: Message ID from request
        :param result: Result data
        :return: Response dictionary
        """
        return {
            'jsonrpc': '2.0',
            'id': msg_id,
            'result': result,
        }

    @classmethod
    def format_error(cls, msg_id, code, message, data=None):
        """
        Format a JSON-RPC error response.

        :param msg_id: Message ID from request (can be None)
        :param code: Error code
        :param message: Error message
        :param data: Optional error data
        :return: Response dictionary
        """
        error = {
            'code': code,
            'message': message,
        }
        if data is not None:
            error['data'] = data

        return {
            'jsonrpc': '2.0',
            'id': msg_id,
            'error': error,
        }

    @classmethod
    def get_server_capabilities(cls):
        """
        Get MCP server capabilities.

        Per MCP spec, server capabilities include:
        - tools: Exposes callable tools (with optional listChanged)
        - resources: Provides readable resources (with optional subscribe, listChanged)
        - prompts: Offers prompt templates (with optional listChanged)
        - logging: Emits structured log messages
        - completions: Supports argument autocompletion

        :return: Capabilities dictionary
        """
        return {
            'tools': {
                'listChanged': False,
            },
            'resources': {
                'subscribe': False,
                'listChanged': False,
            },
            'prompts': {
                'listChanged': False,
            },
            'logging': {},
            #'completions': {}, # disable for now
        }

    @classmethod
    def get_server_info(cls, env):
        """
        Get MCP server info.

        Per MCP spec, serverInfo includes:
        - name: Server identifier (required)
        - version: Server version (required)
        - title: Display name (optional)
        - description: Server description (optional)
        - icons: Array of icon objects (optional)
        - websiteUrl: Server website (optional)

        :param env: Odoo environment
        :return: Server info dictionary
        """
        config = env['mcp.config'].sudo().get_config()
        server_name = config.server_name or 'Odoo MCP Server'

        # Try to get module version
        module = env['ir.module.module'].sudo().search([
            ('name', '=', 'odoo_remote_mcp'),
            ('state', '=', 'installed'),
        ], limit=1)
        version = module.installed_version if module else '1.0.0'

        return {
            'name': server_name,
            'version': version,
            'description': 'MCP server providing access to Odoo ERP functionality',
        }

    @classmethod
    def validate_initialize_params(cls, params):
        """
        Validate initialize request parameters.

        Per MCP spec, initialize request MUST include:
        - protocolVersion: String - the protocol version the client supports
        - capabilities: Object - client capabilities
        - clientInfo: Object - information about the client
          - name: String (required)
          - version: String (required)

        :param params: Initialize request parameters
        :raises MCPError: If required parameters missing or invalid
        """
        # Check protocolVersion (required)
        protocol_version = params.get('protocolVersion')
        if not protocol_version:
            raise MCPError(
                cls.INVALID_PARAMS,
                "Missing required parameter: protocolVersion"
            )
        if not isinstance(protocol_version, str):
            raise MCPError(
                cls.INVALID_PARAMS,
                "protocolVersion must be a string"
            )

        # Check capabilities (required, but can be empty object)
        capabilities = params.get('capabilities')
        if capabilities is None:
            raise MCPError(
                cls.INVALID_PARAMS,
                "Missing required parameter: capabilities"
            )
        if not isinstance(capabilities, dict):
            raise MCPError(
                cls.INVALID_PARAMS,
                "capabilities must be an object"
            )

        # Check clientInfo (required)
        client_info = params.get('clientInfo')
        if not client_info:
            raise MCPError(
                cls.INVALID_PARAMS,
                "Missing required parameter: clientInfo"
            )
        if not isinstance(client_info, dict):
            raise MCPError(
                cls.INVALID_PARAMS,
                "clientInfo must be an object"
            )

        # Check clientInfo.name (required)
        if not client_info.get('name'):
            raise MCPError(
                cls.INVALID_PARAMS,
                "Missing required parameter: clientInfo.name"
            )

        # Check clientInfo.version (required)
        if not client_info.get('version'):
            raise MCPError(
                cls.INVALID_PARAMS,
                "Missing required parameter: clientInfo.version"
            )

    @classmethod
    def handle_initialize(cls, env, params):
        """
        Handle initialize request.

        Per MCP spec:
        1. Validate required params (protocolVersion, capabilities, clientInfo)
        2. Negotiate protocol version
        3. Return server capabilities and info

        The initialize request MUST be the first interaction and MUST NOT
        be part of a JSON-RPC batch.

        :param env: Odoo environment
        :param params: Request parameters
        :return: Response dictionary
        """
        # Validate required parameters
        cls.validate_initialize_params(params)

        # Extract and validate protocol version
        client_protocol_version = params.get('protocolVersion')
        negotiated_version = cls.validate_protocol_version(client_protocol_version)

        client_info = params.get('clientInfo', {})

        _logger.info(
            "MCP client connecting: %s v%s (protocol: %s)",
            client_info.get('name', 'Unknown'),
            client_info.get('version', 'Unknown'),
            negotiated_version
        )

        ICP = env['ir.config_parameter'].sudo()
        instructions = ICP.get_param(
            'mcp.instructions',
            'This server provides access to Odoo ERP data and operations.'
        )

        return {
            'protocolVersion': negotiated_version,
            'serverInfo': cls.get_server_info(env),
            'capabilities': cls.get_server_capabilities(),
            'instructions': instructions,
        }

    @classmethod
    def handle_ping(cls, env, params):
        """
        Handle ping request.

        Per MCP spec, ping can be sent at any time (even before initialization).
        """
        return {}

    @classmethod
    def handle_notifications_initialized(cls, env, params):
        """
        Handle initialized notification.

        Per MCP spec:
        - Client MUST send this after receiving successful initialize response
        - Server SHOULD NOT send requests (other than ping/logging) before this
        - This notification has no params and no response

        :param env: Odoo environment
        :param params: Notification parameters (typically empty)
        """
        _logger.info("MCP client initialized")
        return None  # Notifications don't have responses

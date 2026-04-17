# -*- coding: utf-8 -*-
"""
MCP WebApp Endpoint Model

Stores custom API endpoints for web applications.
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from ..services.executor import MCPExecutor


class MCPWebAppEndpoint(models.Model):
    _name = 'mcp.webapp.endpoint'
    _description = 'MCP WebApp API Endpoint'
    _order = 'endpoint_path, method'

    # -------------------------------------------------------------------------
    # Core Fields
    # -------------------------------------------------------------------------
    webapp_id = fields.Many2one(
        'mcp.webapp',
        string='Web Application',
        required=True,
        ondelete='cascade',
        index=True,
    )
    name = fields.Char(
        string='Name',
        required=True,
        help='Endpoint name for identification',
    )
    endpoint_path = fields.Char(
        string='Endpoint Path',
        required=True,
        help='Relative path for the endpoint (e.g., "orders", "orders/:id", "users/:userId/orders")',
    )
    method = fields.Selection([
        ('GET', 'GET'),
        ('POST', 'POST'),
        ('PUT', 'PUT'),
        ('DELETE', 'DELETE'),
    ], string='HTTP Method', default='GET', required=True,
        help='HTTP method this endpoint responds to')

    # -------------------------------------------------------------------------
    # Handler Code
    # -------------------------------------------------------------------------
    handler_code = fields.Text(
        string='Handler Code (Python)',
        required=True,
        help='Python code that handles the request. '
             'Available variables: query_params (dict), body (dict for POST/PUT), route_params (dict), '
             'request (Odoo HTTP request — session, headers, remote_addr), '
             'mcp_webapp_id (int), mcp_endpoint_id (int). '
             'Assign output to `result` variable.',
    )

    # -------------------------------------------------------------------------
    # Methods
    # -------------------------------------------------------------------------
    def execute_handler(self, query_params=None, body=None, route_params=None, env=None, http_request=None):
        """
        Execute handler_code and return result.

        :param query_params: Dict of query string parameters
        :param body: Dict of request body (for POST/PUT)
        :param route_params: Dict of route parameters extracted from URL
        :param env: Optional environment to use for execution.
                    If not provided, uses self.env (caller's permissions).
                    Pass request.env to execute with visitor's permissions.
        :param http_request: Odoo HTTP request object (odoo.http.request).
                             Provides access to session, headers, remote address, etc.
        :return: Result from handler_code execution
        """
        self.ensure_one()
        if not self.handler_code:
            return {'error': 'No handler code defined'}

        # Add request context and record IDs to the execution
        extra_locals = {
            'query_params': query_params or {},
            'body': body or {},
            'route_params': route_params or {},
            'mcp_webapp_id': self.webapp_id.id,
            'mcp_endpoint_id': self.id,
            'request': http_request,
        }
        execution_env = env if env is not None else self.env
        return MCPExecutor.execute(execution_env, self.handler_code, extra_locals=extra_locals)

    @api.constrains('endpoint_path')
    def _check_endpoint_path(self):
        """Validate endpoint path format."""
        for record in self:
            if not record.endpoint_path:
                continue
            path = record.endpoint_path.strip()
            # Path should not start with / for API endpoints (it's relative to /mcp/webapp/{id}/api/)
            if path.startswith('/'):
                raise ValidationError(
                    f"Endpoint path should not start with '/': {path}. "
                    "It's relative to /mcp/webapp/{{id}}/api/"
                )

    _sql_constraints = [
        ('unique_endpoint_method',
         'UNIQUE(webapp_id, endpoint_path, method)',
         'Each endpoint path + method combination must be unique within a webapp'),
    ]

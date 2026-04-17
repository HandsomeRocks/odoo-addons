# -*- coding: utf-8 -*-
"""
MCP WebApp Page Model

Stores pages/routes for web applications with data fetching and React components.
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from ..services.executor import MCPExecutor


class MCPWebAppPage(models.Model):
    _name = 'mcp.webapp.page'
    _description = 'MCP WebApp Page/Route'
    _order = 'sequence, id'

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
        help='Page name for identification',
    )
    route_path = fields.Char(
        string='Route Path',
        required=True,
        help='React Router path (e.g., "/", "/orders/:id", "/users/:userId/orders")',
    )

    # -------------------------------------------------------------------------
    # Data & Component Code
    # -------------------------------------------------------------------------
    data_code = fields.Text(
        string='Data Code (Python)',
        help='Python code that fetches page data. '
             'Available variables: route_params (dict with path params), '
             'mcp_webapp_id (int), mcp_page_id (int). '
             'Assign output to `result` variable.',
    )
    component_code = fields.Text(
        string='Component Code (JSX)',
        required=True,
        help='React functional component JSX. '
             'Available props: data, routeParams, globalState, setGlobalState, initialData, api',
    )

    # -------------------------------------------------------------------------
    # Component Files
    # -------------------------------------------------------------------------
    component_file_ids = fields.One2many(
        'mcp.webapp.page.file',
        'page_id',
        string='Component Files',
        help='Named code files injected before the main component. '
             'Use for splitting large components into logical sections.',
    )

    # -------------------------------------------------------------------------
    # Page Configuration
    # -------------------------------------------------------------------------
    page_title = fields.Char(
        string='Page Title',
        help='Browser tab title when this page is active',
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order in page list',
    )

    # -------------------------------------------------------------------------
    # Methods
    # -------------------------------------------------------------------------
    def fetch_page_data(self, route_params=None, env=None):
        """
        Execute data_code and return result dict.

        :param route_params: Dict of route parameters extracted from URL
        :param env: Optional environment to use for execution.
                    If not provided, uses self.env (caller's permissions).
                    Pass request.env to execute with visitor's permissions.
        :return: Result dict from data_code execution
        """
        self.ensure_one()
        if not self.data_code:
            return {}

        # Add route_params and record IDs to the execution context
        extra_locals = {
            'route_params': route_params or {},
            'mcp_webapp_id': self.webapp_id.id,
            'mcp_page_id': self.id,
        }
        execution_env = env if env is not None else self.env
        return MCPExecutor.execute(execution_env, self.data_code, extra_locals=extra_locals)

    @api.constrains('route_path')
    def _check_route_path(self):
        """Validate route path format."""
        for record in self:
            if not record.route_path:
                continue
            path = record.route_path.strip()
            if not path.startswith('/'):
                raise ValidationError(
                    f"Route path must start with '/': {path}"
                )

    def get_component_name(self):
        """Generate a valid JavaScript component name from the page name."""
        self.ensure_one()
        # Convert page name to PascalCase component name
        name = self.name or f'Page{self.id}'
        # Remove special characters and capitalize words
        words = ''.join(c if c.isalnum() or c == ' ' else ' ' for c in name).split()
        return ''.join(word.capitalize() for word in words) + 'Page'

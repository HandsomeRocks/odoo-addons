# -*- coding: utf-8 -*-
import secrets

from odoo import api, fields, models


class MCPApiKey(models.Model):
    _name = 'mcp.api.key'
    _description = 'MCP API Key'
    _order = 'create_date desc'

    name = fields.Char(
        string='Name',
        required=True,
        help='Unique identifier for code lookup, e.g. "helpdesk-bot"',
    )
    description = fields.Text(
        string='Description',
        help='What this API key is used for',
    )
    user_id = fields.Many2one(
        'res.users',
        string='User',
        required=True,
        ondelete='cascade',
        help='Which Odoo user this key authenticates as',
    )
    api_key = fields.Char(
        string='API Key',
        copy=False,
        groups='odoo_remote_mcp.group_mcp_admin',
    )
    scope = fields.Char(
        string='Scope',
        default='odoo.read odoo.write odoo.execute offline_access',
    )
    active = fields.Boolean(
        default=True,
        help='Deactivate to revoke this key without deleting it',
    )

    _sql_constraints = [
        ('unique_name', 'UNIQUE(name)', 'API key name must be unique.'),
    ]

    def action_generate_key(self):
        """Generate a new API key."""
        for record in self:
            record.api_key = secrets.token_urlsafe(32)

    @api.model
    def validate_api_key(self, key):
        """
        Validate an API key and return token data if valid.

        :param key: The API key string to validate
        :return: dict with user_id, scope, api_key_id or None
        """
        if not key:
            return None
        api_key_rec = self.sudo().search([
            ('api_key', '=', key),
            ('active', '=', True),
        ], limit=1)
        if not api_key_rec:
            return None
        return {
            'user_id': api_key_rec.user_id.id,
            'scope': api_key_rec.scope or '',
            'api_key_id': api_key_rec.id,
        }

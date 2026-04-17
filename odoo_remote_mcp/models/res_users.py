# -*- coding: utf-8 -*-
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    mcp_oauth_token_ids = fields.One2many(
        'mcp.oauth.token',
        'user_id',
        string='MCP Access Tokens',
    )

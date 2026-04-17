# -*- coding: utf-8 -*-
"""
MCP Tag Model

Simple tagging system for categorizing webapps and echarts.
"""

from odoo import fields, models


class MCPTag(models.Model):
    _name = 'mcp.tag'
    _description = 'MCP Tag'
    _order = 'sequence, name'

    name = fields.Char(
        string='Name',
        required=True,
        translate=True,
    )
    color = fields.Integer(
        string='Color',
        default=0,
        help='Tag color index for kanban views',
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order',
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Uncheck to archive this tag',
    )

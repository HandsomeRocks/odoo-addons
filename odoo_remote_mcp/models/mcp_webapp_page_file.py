# -*- coding: utf-8 -*-
"""
MCP WebApp Page Component File

Stores named code files for a webapp page, allowing large components
to be split into logical sections (engine, sprites, levels, etc.).
Files are injected in sequence order before the main component_code.
"""

from odoo import fields, models


class MCPWebAppPageFile(models.Model):
    _name = 'mcp.webapp.page.file'
    _description = 'MCP WebApp Page Component File'
    _order = 'sequence, id'

    page_id = fields.Many2one(
        'mcp.webapp.page',
        string='Page',
        required=True,
        ondelete='cascade',
        index=True,
    )
    name = fields.Char(
        string='File Name',
        required=True,
        help='Descriptive file name (e.g. "engine.js", "sprites.js", "levels.js")',
    )
    code = fields.Text(
        string='Code',
        required=True,
        help='JavaScript/JSX code. Injected in the same script scope before the main component.',
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Injection order. Lower sequence files are injected first.',
    )

    _sql_constraints = [
        ('unique_page_file_name', 'UNIQUE(page_id, name)',
         'File names must be unique within a page.'),
    ]

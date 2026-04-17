# -*- coding: utf-8 -*-
from odoo import fields, models


class MCPModelGroupLine(models.Model):
    _name = 'mcp.model.group.line'
    _description = 'MCP Model Group Line'
    _order = 'model_name'

    group_id = fields.Many2one('mcp.model.group', string='Model Group', required=True, ondelete='cascade', index=True)
    model_id = fields.Many2one('ir.model', string='Model', required=True, ondelete='cascade', index=True)
    model_name = fields.Char(related='model_id.model', string='Model Name', store=True, index=True)

    # Block flags - True = operation is BLOCKED, False = operation is allowed
    # Default True to block all operations when adding a model to restrictions
    perm_read = fields.Boolean(string='Block Read', default=True, help='If checked, read operations are blocked')
    perm_create = fields.Boolean(string='Block Create', default=True, help='If checked, create operations are blocked')
    perm_write = fields.Boolean(string='Block Write', default=True, help='If checked, write operations are blocked')
    perm_unlink = fields.Boolean(string='Block Delete', default=True, help='If checked, delete operations are blocked')

    _sql_constraints = [
        ('unique_group_model', 'UNIQUE(group_id, model_id)',
         'Each model can only appear once per model group.'),
    ]

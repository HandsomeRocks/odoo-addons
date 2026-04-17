# -*- coding: utf-8 -*-
from odoo import api, fields, models


class MCPModelGroup(models.Model):
    _name = 'mcp.model.group'
    _description = 'MCP Model Group'
    _order = 'name'

    name = fields.Char(string='Name', required=True,
                       help='Descriptive name for this model group (e.g., "Restricted Models", "Sensitive Data")')
    model_line_ids = fields.One2many(
        'mcp.model.group.line',
        'group_id',
        string='Restricted Models',
        help='Models blocked from MCP access. Check operations to block them.',
    )
    restricted_fields = fields.Many2many(
        'ir.model.fields',
        'mcp_model_group_restricted_fields_rel',
        'group_id',
        'field_id',
        string='Restricted Fields',
        help='Fields completely blocked from MCP access. Hidden from reads (search_read, '
             'read_record, read_group, get_model_schema) and rejected on writes (create_record, update_record).',
    )
    model_count = fields.Integer(
        string='Model Count',
        compute='_compute_model_count',
    )
    restricted_field_count = fields.Integer(
        string='Restricted Field Count',
        compute='_compute_restricted_field_count',
    )

    def _compute_model_count(self):
        for record in self:
            record.model_count = len(record.model_line_ids)

    def _compute_restricted_field_count(self):
        for record in self:
            record.restricted_field_count = len(record.restricted_fields)

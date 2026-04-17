# -*- coding: utf-8 -*-
from odoo import api, fields, models


class MCPModelGroupAddModels(models.TransientModel):
    _name = 'mcp.model.group.add.models'
    _description = 'Add Restricted Models to MCP Model Group'

    group_id = fields.Many2one(
        'mcp.model.group',
        string='Model Group',
        required=True,
        readonly=True,
    )
    model_ids = fields.Many2many(
        'ir.model',
        string='Models to Restrict',
        required=True,
        help='Select models to block from MCP access',
    )

    # Block flags - True = operation is BLOCKED (default: block all)
    perm_read = fields.Boolean(string='Block Read', default=True, help='Block read operations')
    perm_create = fields.Boolean(string='Block Create', default=True, help='Block create operations')
    perm_write = fields.Boolean(string='Block Write', default=True, help='Block write operations')
    perm_unlink = fields.Boolean(string='Block Delete', default=True, help='Block delete operations')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'mcp.model.group':
            res['group_id'] = self.env.context.get('active_id')
        return res

    def action_add_models(self):
        """Create model group lines for selected models."""
        self.ensure_one()

        # Get existing models in the group
        existing_models = self.group_id.model_line_ids.mapped('model_id')

        # Create lines only for new models
        new_models = self.model_ids - existing_models

        lines_data = [
            {
                'group_id': self.group_id.id,
                'model_id': model.id,
                'perm_read': self.perm_read,
                'perm_create': self.perm_create,
                'perm_write': self.perm_write,
                'perm_unlink': self.perm_unlink,
            }
            for model in new_models
        ]

        if lines_data:
            self.env['mcp.model.group.line'].create(lines_data)

        return {'type': 'ir.actions.act_window_close'}

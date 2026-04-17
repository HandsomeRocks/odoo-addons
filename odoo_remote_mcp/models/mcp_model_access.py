# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class MCPModelAccess(models.Model):
    _name = 'mcp.model.access'
    _description = 'MCP Model Access'
    _order = 'name'

    name = fields.Char(
        string='Name',
        required=True,
        help='Descriptive name for this access configuration',
    )
    model_group_id = fields.Many2one(
        'mcp.model.group',
        string='Model/Field Group',
        required=True,
        ondelete='cascade',
        help='The group of models accessible to users in this configuration',
    )
    user_ids = fields.Many2many(
        'res.users',
        'mcp_model_access_users_rel',
        'access_id',
        'user_id',
        string='Applicable Users',
        help='Users this configuration applies to. Leave empty for default (all users).',
    )
    config_id = fields.Many2one(
        'mcp.config',
        string='Configuration',
        required=True,
        ondelete='cascade',
    )
    is_default = fields.Boolean(
        string='Is Default',
        compute='_compute_is_default',
        store=True,
    )

    _sql_constraints = [
        ('model_group_unique', 'UNIQUE(model_group_id)', 'Each model group can only be assigned to one access configuration.'),
    ]

    @api.depends('user_ids')
    def _compute_is_default(self):
        for record in self:
            record.is_default = not record.user_ids

    @api.constrains('user_ids')
    def _check_user_uniqueness(self):
        """Ensure user is not in multiple access configurations."""
        for record in self:
            if not record.user_ids:
                continue
            for user in record.user_ids:
                other_configs = self.search([
                    ('id', '!=', record.id),
                    ('config_id', '=', record.config_id.id),
                    ('user_ids', 'in', user.id),
                ])
                if other_configs:
                    raise ValidationError(
                        f"User '{user.name}' is already in access configuration "
                        f"'{other_configs[0].name}'. A user can only be in one configuration."
                    )

    @api.constrains('user_ids', 'config_id')
    def _check_single_default(self):
        """Ensure only one default configuration (empty user_ids) exists."""
        for record in self:
            if record.user_ids:
                continue  # Not a default configuration
            other_defaults = self.search([
                ('id', '!=', record.id),
                ('config_id', '=', record.config_id.id),
                ('user_ids', '=', False),
            ])
            if other_defaults:
                raise ValidationError(
                    f"A default access configuration '{other_defaults[0].name}' already exists. "
                    f"Only one configuration can have empty 'Applicable Users'."
                )

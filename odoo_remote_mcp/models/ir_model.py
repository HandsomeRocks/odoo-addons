# -*- coding: utf-8 -*-
from odoo import fields, models


class IrModel(models.Model):
    _inherit = 'ir.model'

    # Redefine modules field to add search capability
    modules = fields.Char(search='_search_modules')

    def _search_modules(self, operator, value):
        """
        Custom search for the computed 'modules' field.

        Since modules is computed from ir.model.data, we search there
        and return matching model IDs. Only considers installed modules
        to match the compute logic in _in_modules.
        """
        # Get installed module names to match compute behavior
        installed_names = set(
            self.env['ir.module.module'].sudo().search([
                ('state', '=', 'installed')
            ]).mapped('name')
        )

        models_with_installed = None  # Lazy load

        def get_models_with_installed():
            nonlocal models_with_installed
            if models_with_installed is None:
                models_with_installed = self.env['ir.model.data'].sudo().search([
                    ('model', '=', 'ir.model'),
                    ('module', 'in', list(installed_names)),
                ]).mapped('res_id')
            return models_with_installed

        if not value:
            if operator in ('=', 'in'):
                # Searching for empty/no modules - find models with no installed module references
                return [('id', 'not in', get_models_with_installed())]
            elif operator in ('!=', 'not in'):
                # Searching for non-empty modules - find models WITH installed module references
                return [('id', 'in', get_models_with_installed())]
            return []

        IrModelData = self.env['ir.model.data'].sudo()

        if operator == 'in':
            # value is a list of module names - only search installed ones
            if not isinstance(value, (list, tuple)):
                value = [value]
            # Intersect with installed modules
            search_modules = list(set(value) & installed_names)
            if not search_modules:
                return [('id', '=', False)]
            model_data = IrModelData.search([
                ('model', '=', 'ir.model'),
                ('module', 'in', search_modules),
            ])
            return [('id', 'in', model_data.mapped('res_id'))]

        elif operator == 'not in':
            if not isinstance(value, (list, tuple)):
                value = [value]
            # Intersect with installed modules
            search_modules = list(set(value) & installed_names)
            if not search_modules:
                return []  # Nothing to exclude
            model_data = IrModelData.search([
                ('model', '=', 'ir.model'),
                ('module', 'in', search_modules),
            ])
            return [('id', 'not in', model_data.mapped('res_id'))]

        elif operator in ('ilike', 'like', '=ilike', '=like', '='):
            # For '=' we use 'ilike' since modules is comma-separated
            # and exact match on full string rarely makes sense
            op = 'ilike' if operator == '=' else operator
            model_data = IrModelData.search([
                ('model', '=', 'ir.model'),
                ('module', op, value),
                ('module', 'in', list(installed_names)),
            ])
            return [('id', 'in', model_data.mapped('res_id'))]

        elif operator in ('not ilike', 'not like', '!='):
            model_data = IrModelData.search([
                ('model', '=', 'ir.model'),
                ('module', 'ilike', value),
                ('module', 'in', list(installed_names)),
            ])
            return [('id', 'not in', model_data.mapped('res_id'))]

        # Unsupported operator
        return [('id', '=', False)]

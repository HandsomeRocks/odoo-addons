# -*- coding: utf-8 -*-
from odoo import models


class IrModuleModule(models.Model):
    _inherit = 'ir.module.module'

    def module_uninstall(self):
        # Find mcp.module records linked to modules being uninstalled
        mcp_modules = self.env['mcp.module'].sudo().search([
            ('installed_module_id', 'in', self.ids),
        ])
        res = super().module_uninstall()
        if mcp_modules:
            # Trigger recompute of state now that ir.module.module state changed
            mcp_modules._compute_state()
        return res

# -*- coding: utf-8 -*-
"""
MCP Module File Model

Individual files within an MCP importable module (XML, CSV, static assets, etc.).
The __manifest__.py is treated like any other file — the AI/user writes it directly.
"""

from odoo import api, fields, models

# Fields that represent actual file content — changes to these mean the
# installed module is potentially out of sync.
_CONTENT_FIELDS = {'content', 'binary_content', 'file_path', 'active'}


class MCPModuleFile(models.Model):
    _name = 'mcp.module.file'
    _description = 'MCP Module File'
    _order = 'sequence, id'

    module_id = fields.Many2one('mcp.module', required=True, ondelete='cascade')
    file_path = fields.Char(
        required=True,
        help="Relative path within module, e.g. '__manifest__.py', 'data/models.xml', "
             "'security/ir.model.access.csv'",
    )
    content = fields.Text(help="Text content for py/xml/csv/sql/po files")
    file_ext = fields.Char(compute='_compute_file_ext')
    binary_content = fields.Binary(help="Binary content for static assets (images, etc.)")
    sequence = fields.Integer(default=10, help="Display order in the file list")
    active = fields.Boolean(default=True)

    @api.depends('file_path')
    def _compute_file_ext(self):
        for rec in self:
            rec.file_ext = ('.' + rec.file_path.rsplit('.', 1)[-1]) if rec.file_path and '.' in rec.file_path else ''

    _sql_constraints = [
        ('file_path_uniq', 'UNIQUE(module_id, file_path)', 'File path must be unique per module.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped('module_id').write({'files_changed': True})
        return records

    def write(self, vals):
        res = super().write(vals)
        if _CONTENT_FIELDS & set(vals):
            self.mapped('module_id').write({'files_changed': True})
        return res

    def unlink(self):
        modules = self.mapped('module_id')
        res = super().unlink()
        modules.write({'files_changed': True})
        return res

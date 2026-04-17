# -*- coding: utf-8 -*-
"""
MCP Module Model

Stores importable Odoo data modules that can be packaged as ZIPs and
installed via base_import_module's _import_zipfile() mechanism.

The __manifest__.py is a regular file written by the user/AI — the model
does not duplicate manifest fields.  Only lightweight metadata (name,
technical_name) lives on the record; everything else is in the files.
"""

import ast
import base64
import csv
import io
import zipfile
from io import BytesIO

from odoo import api, fields, models
from odoo.exceptions import UserError


class MCPModule(models.Model):
    _name = 'mcp.module'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'MCP Importable Module'
    _order = 'name'

    name = fields.Char(required=True, tracking=True, help="Display name")
    technical_name = fields.Char(
        required=True,
        tracking=True,
        help="Technical name (auto-prefixed with x_ if not already)",
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('packaged', 'Packaged'),
        ('installed', 'Installed'),
    ], compute='_compute_state', store=True, tracking=True)

    files_changed = fields.Boolean(
        string='Files out of sync with installed module?',
        default=False,
        readonly=True,
        tracking=True,
        help="True when module files have been modified since the last "
             "successful install. Used to track sync state between files "
             "and the installed module in Odoo.",
    )

    zip_file = fields.Binary(attachment=True, readonly=True)
    zip_filename = fields.Char()
    file_ids = fields.One2many('mcp.module.file', 'module_id', string='Files')
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    last_error = fields.Text(readonly=True)
    installed_module_id = fields.Many2one(
        'ir.module.module',
        string='Installed Module',
        readonly=True,
        help="Link to the ir.module.module record created after import",
    )

    _sql_constraints = [
        ('technical_name_uniq', 'UNIQUE(technical_name)', 'Technical name must be unique.'),
    ]

    @api.depends('installed_module_id', 'zip_file')
    def _compute_state(self):
        for rec in self:
            if rec.installed_module_id and rec.installed_module_id.state == 'installed':
                rec.state = 'installed'
            elif rec.zip_file:
                rec.state = 'packaged'
            else:
                rec.state = 'draft'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('technical_name'):
                vals['technical_name'] = self._ensure_x_prefix(vals['technical_name'])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('technical_name'):
            vals['technical_name'] = self._ensure_x_prefix(vals['technical_name'])
        return super().write(vals)

    @staticmethod
    def _ensure_x_prefix(technical_name):
        """Auto-prefix x_ on technical_name if missing."""
        technical_name = technical_name.strip().lower().replace('-', '_').replace(' ', '_')
        if not technical_name.startswith('x_'):
            technical_name = f'x_{technical_name}'
        return technical_name

    def _get_manifest_file(self):
        """Return the __manifest__.py file record, or False."""
        self.ensure_one()
        return self.file_ids.filtered(
            lambda f: f.active and f.file_path == '__manifest__.py'
        )[:1]

    def _parse_manifest(self):
        """Parse __manifest__.py content and return as dict, or None on failure."""
        self.ensure_one()
        manifest_file = self._get_manifest_file()
        if not manifest_file or not manifest_file.content:
            return None
        try:
            return ast.literal_eval(manifest_file.content.strip())
        except (ValueError, SyntaxError):
            return None

    def _validate_manifest_files(self):
        """Check that files referenced in manifest 'data' list exist as records.

        :returns: list of warning strings (unreferenced files)
        :raises UserError: if manifest references files that don't exist
        """
        self.ensure_one()
        manifest = self._parse_manifest()
        if not manifest:
            return []

        data_files = (
            manifest.get('data', [])
            + manifest.get('init_xml', [])
            + manifest.get('update_xml', [])
        )
        existing_paths = set(
            self.file_ids.filtered('active').mapped('file_path')
        )

        # Block on missing files — these will cause install failure
        missing = [f for f in data_files if f not in existing_paths]
        if missing:
            raise UserError(
                "Files referenced in __manifest__.py 'data' list do not exist "
                "as module files:\n"
                + "\n".join(f"  - {f}" for f in missing)
                + "\n\nCreate these files first, or remove them from the manifest."
            )

        # Warn about unreferenced data files (silently ignored on install)
        data_set = set(data_files)
        unreferenced = [
            f for f in existing_paths
            if f.endswith(('.xml', '.csv'))
            and f != '__manifest__.py'
            and f not in data_set
            and not f.startswith('static/')
            and not f.startswith('i18n/')
        ]
        return unreferenced

    def action_package(self):
        """Generate ZIP file from module files."""
        self.ensure_one()
        if not self._get_manifest_file():
            raise UserError(
                "Module must have a '__manifest__.py' file. "
                "Create one with file_path='__manifest__.py'."
            )

        # Validate manifest references before building ZIP
        self._validate_manifest_files()

        buf = BytesIO()
        tech = self.technical_name

        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in self.file_ids.filtered('active'):
                if f.binary_content:
                    zf.writestr(f'{tech}/{f.file_path}', base64.b64decode(f.binary_content))
                else:
                    # Write text content, or an empty file if no content
                    # (e.g. __init__.py needs to exist but can be empty)
                    zf.writestr(f'{tech}/{f.file_path}', f.content or '')

        self.write({
            'zip_file': base64.b64encode(buf.getvalue()),
            'zip_filename': f'{tech}.zip',
            'last_error': False,
        })

    def action_uninstall_wizard(self):
        """Open the standard Odoo uninstall wizard (shows dependencies & warnings)."""
        self.ensure_one()
        ir_module = self.installed_module_id or self.env['ir.module.module'].sudo().search(
            [('name', '=', self.technical_name)], limit=1,
        )
        if not ir_module or ir_module.state != 'installed':
            raise UserError(
                f"Module '{self.technical_name}' is not installed in Odoo."
            )
        return ir_module.sudo().button_uninstall_wizard()

    def action_uninstall(self):
        """Uninstall the module directly (used by MCP tool for uninstall_first)."""
        self.ensure_one()
        ir_module = self.installed_module_id or self.env['ir.module.module'].sudo().search(
            [('name', '=', self.technical_name)], limit=1,
        )
        if not ir_module or ir_module.state != 'installed':
            raise UserError(
                f"Module '{self.technical_name}' is not installed in Odoo."
            )
        ir_module.sudo().module_uninstall()
        self.write({
            'installed_module_id': False,
            'last_error': False,
            'files_changed': False,
        })
        self.message_post(body="Module uninstalled")

    def action_install(self, force=False, uninstall_first=False):
        """Import module ZIP into Odoo via base_import_module.

        :param force: Force re-init (overwrites noupdate=1 records)
        :param uninstall_first: Uninstall existing module before importing,
            which cleanly drops all previous tables/columns/data. Recommended
            when field types change or fields are removed between versions.
        :returns: notification action dict for the UI
        """
        self.ensure_one()

        # Always repackage to ensure ZIP matches current files
        self.action_package()

        # Uninstall first if requested and a previous version exists
        if uninstall_first:
            ir_module = self.installed_module_id or self.env['ir.module.module'].sudo().search(
                [('name', '=', self.technical_name)], limit=1,
            )
            if ir_module and ir_module.state == 'installed':
                ir_module.sudo().module_uninstall()
                self.message_post(body="Module uninstalled (prior to reinstall)")

        zip_data = base64.b64decode(self.zip_file)
        fp = BytesIO(zip_data)

        error_msg, module_names = (
            self.env['ir.module.module'].sudo()._import_zipfile(fp, force=force)
        )

        # Link to the ir.module.module record
        ir_module = self.env['ir.module.module'].sudo().search(
            [('name', '=', self.technical_name)], limit=1,
        )

        self.write({
            'last_error': error_msg or False,
            'installed_module_id': ir_module.id if ir_module else False,
            'files_changed': False,
        })

        # Post to chatter for traceability
        label = "reinstalled (uninstall+install)" if uninstall_first else "installed"
        body = f"Module {label} (force={force})"
        if error_msg:
            body += f"<br/><b>Warnings:</b> {error_msg}"
        config = self.env['mcp.config'].get_config()
        attachments = [(self.zip_filename, zip_data)] if config.module_post_zip_to_chatter else []
        self.message_post(
            body=body,
            attachments=attachments,
        )

        msg = f"Module '{self.name}' {label} successfully."
        if error_msg:
            msg += f"\nWarnings: {error_msg}"
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Module Installed',
                'message': msg,
                'type': 'success' if not error_msg else 'warning',
                'sticky': bool(error_msg),
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_open_installed_module(self):
        """Open the ir.module.module form for the installed module."""
        self.ensure_one()
        if not self.installed_module_id:
            raise UserError("Module has not been installed yet.")
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ir.module.module',
            'res_id': self.installed_module_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_export(self):
        """Export module and its files as CSV and post to chatter.

        Uses CSV format instead of XLSX to avoid the 32,767 character cell
        limit that corrupts large code fields.  Includes external IDs so the
        CSV can be re-imported to recreate the module record and files.
        """
        self.ensure_one()

        fields_to_export = [
            'id', 'name', 'technical_name',
            'file_ids/id', 'file_ids/file_path',
            'file_ids/sequence', 'file_ids/content',
            'file_ids/binary_content',
        ]

        export_result = self.export_data(fields_to_export)

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_ALL)
        writer.writerow(fields_to_export)

        for row_data in export_result['datas']:
            row = []
            for value in row_data:
                cell_value = value if value is not False else ''
                if isinstance(cell_value, bytes):
                    cell_value = cell_value.decode('utf-8')
                row.append(cell_value)
            writer.writerow(row)

        csv_content = output.getvalue()

        timestamp = fields.Datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{self.technical_name}_{timestamp}.csv"

        csv_bytes = csv_content.encode('utf-8-sig')

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(csv_bytes),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'text/csv',
        })

        self.message_post(
            body=f"Module exported: {filename}",
            attachment_ids=[attachment.id],
        )

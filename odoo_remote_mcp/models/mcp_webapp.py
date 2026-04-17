# -*- coding: utf-8 -*-
"""
MCP WebApp Model

Stores AI-generated React web applications with multi-page routing,
shared components, global state, and API endpoints.
"""

import base64
import csv
import io
import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services.executor import MCPExecutor


class MCPWebApp(models.Model):
    _name = 'mcp.webapp'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'MCP AI-Generated Web Application'
    _order = 'sequence, create_date desc'

    # -------------------------------------------------------------------------
    # Core Fields
    # -------------------------------------------------------------------------
    name = fields.Char(
        string='Name',
        required=True,
        tracking=True,
        help='Application title',
    )
    slug = fields.Char(
        string='URL Slug',
        index=True,
        tracking=True,
        help='Custom URL slug (e.g., "sales-dashboard" creates /sales-dashboard). '
             'Also accessible at /mcp/webapp/sales-dashboard. Leave empty to use numeric ID. '
             'Note: avoid slugs that match existing Odoo routes (e.g., "web", "shop", "mail") '
             'as they will be shadowed — the app will still work at /mcp/webapp/<slug>.',
    )

    _sql_constraints = [
        ('slug_unique', 'UNIQUE(slug)', 'URL slug must be unique.'),
    ]

    @api.constrains('slug')
    def _check_slug_format(self):
        """Validate slug format: lowercase alphanumeric and hyphens only."""
        slug_pattern = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
        for record in self:
            if record.slug:
                if not slug_pattern.match(record.slug):
                    raise ValidationError(
                        "URL slug must be lowercase letters, numbers, and hyphens only "
                        "(e.g., 'sales-dashboard', 'my-app-2'). Cannot start or end with hyphen."
                    )
                if len(record.slug) > 64:
                    raise ValidationError("URL slug must be 64 characters or less.")

    description = fields.Text(
        string='Description',
        tracking=True,
        help='Optional description of what this application does',
    )
    thumbnail = fields.Image(
        string='Thumbnail',
        max_width=512,
        max_height=512,
        help='Thumbnail image for the application',
    )
    tag_ids = fields.Many2many(
        'mcp.tag',
        'mcp_webapp_tag_rel',
        'webapp_id',
        'tag_id',
        string='Tags',
        help='Tags for categorizing this application',
    )
    view_count = fields.Integer(
        string='View Count',
        default=0,
        readonly=True,
        help='Number of times this application has been viewed',
    )
    track_viewcount = fields.Boolean(
        string='Track View Count',
        default=False,
        help='If enabled, increment view_count each time the app is viewed',
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order (lower = first)',
    )

    # -------------------------------------------------------------------------
    # Data & State
    # -------------------------------------------------------------------------
    data_code = fields.Text(
        string='Initial Data Code (Python)',
        help='Python code that fetches initial app data. Assign output to `result` variable. '
             'Available context: mcp_webapp_id (int).',
    )
    global_state_code = fields.Text(
        string='Global State (JavaScript)',
        default='{}',
        help='JavaScript object literal for initial React global state',
    )

    # -------------------------------------------------------------------------
    # Components & Styles
    # -------------------------------------------------------------------------
    shared_components = fields.Text(
        string='Shared Components (JSX)',
        help='Reusable JSX components available to all pages',
    )
    shared_styles = fields.Text(
        string='Shared Styles (CSS)',
        help='Global CSS styles applied to the application',
    )

    # -------------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------------
    router_mode = fields.Selection([
        ('hash', 'Hash Router'),
        ('memory', 'Memory Router'),
    ], string='Router Mode', default='hash',
        help='Hash Router uses URL fragments (works with Odoo.sh), Memory Router keeps history in memory')

    tailwind_enabled = fields.Boolean(
        string='Enable Tailwind CSS',
        default=True,
        help='Include Tailwind CSS CDN for utility-first styling',
    )

    cdn_dependencies = fields.Text(
        string='Additional CDN Dependencies',
        help='Additional CDN URLs to include (one per line). Import map entries in JSON format.',
    )
    custom_imports = fields.Text(
        string='Custom Imports',
        help='Additional ES module imports (e.g., import { LineChart } from "recharts";). Added after standard React/Router imports.',
    )

    # -------------------------------------------------------------------------
    # PWA Configuration
    # -------------------------------------------------------------------------
    pwa_enabled = fields.Boolean(string='PWA Enabled', default=False)
    pwa_theme_color = fields.Char(string='Theme Color', default='#714B67')
    pwa_background_color = fields.Char(string='Background Color', default='#ffffff')
    pwa_display = fields.Selection([
        ('standalone', 'Standalone'),
        ('fullscreen', 'Fullscreen'),
    ], string='Display Mode', default='standalone')

    # -------------------------------------------------------------------------
    # Relations
    # -------------------------------------------------------------------------
    page_ids = fields.One2many(
        'mcp.webapp.page',
        'webapp_id',
        string='Pages',
        help='Application pages/routes', copy=True
    )
    endpoint_ids = fields.One2many(
        'mcp.webapp.endpoint',
        'webapp_id',
        string='API Endpoints',
        help='Custom API endpoints', copy=True
    )
    asset_ids = fields.One2many(
        'ir.attachment', 'res_id',
        domain=[('res_model', '=', 'mcp.webapp'), ('res_field', '=', 'asset_ids')],
        string='Assets',
        help='Binary assets (images, audio, sprites) served via asset() helper in components',
    )
    storage_ids = fields.One2many(
        'mcp.webapp.user.storage',
        'webapp_id',
        string='User Storage',
        help='Per-user persistent storage records'
    )

    # -------------------------------------------------------------------------
    # Ownership
    # -------------------------------------------------------------------------
    user_id = fields.Many2one(
        'res.users',
        string='Created By',
        default=lambda self: self.env.user,
        required=True,
        index=True,
    )
    client_id = fields.Many2one(
        'mcp.oauth.client',
        string='MCP Client',
        ondelete='set null',
        help='OAuth client that created this webapp',
    )

    # -------------------------------------------------------------------------
    # Sharing
    # -------------------------------------------------------------------------
    shared_user_ids = fields.Many2many(
        'res.users',
        'mcp_webapp_shared_users_rel',
        'webapp_id',
        'user_id',
        string='Shared With Users',
        help='Specific users who can access this webapp',
    )
    shared_group_ids = fields.Many2many(
        'res.groups',
        'mcp_webapp_shared_groups_rel',
        'webapp_id',
        'group_id',
        string='Shared With Groups',
        help='Groups whose members can access this webapp (use base.group_portal for portal users, base.group_public for public access)',
    )

    # -------------------------------------------------------------------------
    # Computed URLs
    # -------------------------------------------------------------------------
    app_url = fields.Char(
        string='App URL',
        compute='_compute_app_url',
    )
    embed_code = fields.Char(
        string='Embed Code',
        compute='_compute_embed_code',
    )

    def _compute_app_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for record in self:
            if record.id:
                # Prefer bare slug URL, fall back to /mcp/webapp/<id>
                if record.slug:
                    record.app_url = f"{base_url}/{record.slug}"
                else:
                    record.app_url = f"{base_url}/mcp/webapp/{record.id}"
            else:
                record.app_url = False

    @api.depends('app_url')
    def _compute_embed_code(self):
        for record in self:
            if record.app_url:
                record.embed_code = (
                    f'<iframe src="{record.app_url}" '
                    f'width="100%" height="700" '
                    f'style="border: none;" loading="lazy"></iframe>'
                )
            else:
                record.embed_code = False

    # -------------------------------------------------------------------------
    # Data Fetching
    # -------------------------------------------------------------------------
    def fetch_initial_data(self, env=None):
        """
        Execute data_code and return result dict.

        Uses MCPExecutor from services (same as execute_orm tool).

        :param env: Optional environment to use for execution.
                    If not provided, uses self.env (caller's permissions).
                    Pass request.env to execute with visitor's permissions.
        """
        self.ensure_one()
        if not self.data_code:
            return {}

        execution_env = env if env is not None else self.env
        return MCPExecutor.execute(execution_env, self.data_code, extra_locals={
            'mcp_webapp_id': self.id,
        })

    # -------------------------------------------------------------------------
    # Access Control
    # -------------------------------------------------------------------------
    @api.model
    def get_accessible_webapps(self):
        """
        Get all webapps accessible to the current user.

        A webapp is accessible if:
        - User created it, OR
        - User is in shared_user_ids, OR
        - User is in one of the shared_group_ids
        """
        user = self.env.user
        return self.search([
            '|', '|',
            ('user_id', '=', user.id),
            ('shared_user_ids', 'in', user.ids),
            ('shared_group_ids', 'in', user.groups_id.ids),
        ])

    def _has_access(self, user):
        """
        Check if user has access to this webapp.

        :param user: res.users record
        :return: True if user has access
        """
        self.ensure_one()
        return (
            self.user_id.id == user.id or
            user.id in self.shared_user_ids.ids or
            bool(set(user.groups_id.ids) & set(self.shared_group_ids.ids))
        )

    def action_view_app(self):
        """Open the webapp in a new browser tab."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': self.app_url,
            'target': 'new',
        }

    def increment_view_count(self):
        """Increment the view count for this webapp if tracking is enabled."""
        self.ensure_one()
        if not self.track_viewcount:
            return
        # Use SQL to avoid triggering write rules and for performance
        self.env.cr.execute(
            "UPDATE mcp_webapp SET view_count = view_count + 1 WHERE id = %s",
            (self.id,)
        )

    # -------------------------------------------------------------------------
    # Version Control
    # -------------------------------------------------------------------------
    @api.model
    def load(self, fields, data):
        """Override load to fix readonly ir.attachment fields after import.

        In Odoo 16/17, res_model, res_field, and mimetype are readonly on
        ir.attachment, so the CSV import silently drops those values.  After
        the standard import finishes we patch any attachment records that were
        linked to a webapp via the One2many but are missing those fields.
        """
        result = super().load(fields, data)

        if "import_file" not in self.env.context:
            return result

        # Collect successfully imported webapp IDs
        imported_ids = [rid for rid in (result.get('ids') or []) if rid]
        if imported_ids:
            self.env.cr.execute("""
                UPDATE ir_attachment
                   SET res_model = 'mcp.webapp',
                       res_field = 'asset_ids'
                 WHERE res_id IN %s
                   AND (res_model IS NULL OR res_model = '')
                   AND (res_field IS NULL OR res_field = '')
                   AND type = 'binary'
                   AND name IS NOT NULL
            """, [tuple(imported_ids)])
        return result

    def action_export(self):
        """Export webapp (without asset binary data) as CSV."""
        return self._export_csv(include_assets=False)

    def action_export_with_assets(self):
        """Export webapp with full asset binary data as CSV.

        Warning: large assets (GLB, MP3) can produce CSVs that exceed
        the memory limits of production Odoo workers.
        """
        return self._export_csv(include_assets=True)

    def _export_csv(self, include_assets=False):
        """Export webapp as CSV with external IDs and post to chatter.

        Includes external IDs so the CSV can be reimported on the same or a
        different instance to update the existing record (matched by external
        ID) or create a new one (if the ID doesn't exist on the target).

        Pages and endpoints are exported with their IDs so reimporting updates
        existing ones and creates new ones.  Pages or endpoints that were
        removed since the last export will remain as orphans on the target
        and need manual cleanup.
        """
        self.ensure_one()

        fields_to_export = [
            'id',
            'name', 'slug', 'description', 'thumbnail', 'track_viewcount',
            'data_code', 'global_state_code',
            'shared_components', 'shared_styles', 'router_mode',
            'tailwind_enabled', 'cdn_dependencies', 'custom_imports',
            'pwa_enabled', 'pwa_theme_color', 'pwa_background_color', 'pwa_display',
            'page_ids/id', 'page_ids/name', 'page_ids/route_path',
            'page_ids/page_title', 'page_ids/sequence',
            'page_ids/data_code', 'page_ids/component_code',
            'page_ids/component_file_ids/id',
            'page_ids/component_file_ids/name',
            'page_ids/component_file_ids/code',
            'page_ids/component_file_ids/sequence',
            'endpoint_ids/id', 'endpoint_ids/name',
            'endpoint_ids/endpoint_path',
            'endpoint_ids/method', 'endpoint_ids/handler_code',
        ]

        if include_assets:
            fields_to_export += [
                'asset_ids/id', 'asset_ids/name', 'asset_ids/datas',
                'asset_ids/res_model', 'asset_ids/res_field',
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
        suffix = '_with_assets' if include_assets else ''
        filename = f"{self.name.replace(' ', '_')}_{timestamp}{suffix}.csv"

        csv_bytes = csv_content.encode('utf-8-sig')

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(csv_bytes),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'text/csv',
        })

        asset_note = ''
        if not include_assets and self.asset_ids:
            asset_note = f" ({len(self.asset_ids)} asset(s) excluded)"

        self.message_post(
            body=f"WebApp exported: {filename}{asset_note}",
            attachment_ids=[attachment.id],
        )



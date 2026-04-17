# -*- coding: utf-8 -*-
"""
MCP EChart Model

Stores AI-generated ECharts dashboards with separate data and chart specifications.
"""

import base64
import csv
import io
import json
import secrets

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services.executor import MCPExecutor

# Default extension CDN URLs - popular extensions that cover most use cases
DEFAULT_EXTENSION_URLS = """https://cdn.jsdelivr.net/npm/echarts-gl@2/dist/echarts-gl.min.js
https://cdn.jsdelivr.net/npm/echarts-wordcloud@2/dist/echarts-wordcloud.min.js
https://cdn.jsdelivr.net/npm/echarts-liquidfill@3/dist/echarts-liquidfill.min.js
https://cdn.jsdelivr.net/npm/echarts-stat@latest/dist/ecStat.min.js"""


class MCPEChart(models.Model):
    _name = 'mcp.echart'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'MCP AI-Generated EChart Dashboard'
    _order = 'create_date desc'

    name = fields.Char(
        string='Name',
        required=True,
        tracking=True,
        help='Dashboard title',
    )
    description = fields.Text(
        string='Description',
        tracking=True,
        help='Optional description of what this chart visualizes',
    )
    tag_ids = fields.Many2many(
        'mcp.tag',
        'mcp_echart_tag_rel',
        'echart_id',
        'tag_id',
        string='Tags',
        help='Tags for categorizing this chart',
    )

    # Data fetching code (Python via safe_eval)
    data_code = fields.Text(
        string='Data Code (Python)',
        help='Python code that fetches data. Assign output to `result` variable. '
             'Available context: mcp_chart_id (int).',
    )

    # ECharts options JSON (with $data.xxx placeholders)
    chart_options = fields.Json(
        string='Chart Options (ECharts)',
        help='ECharts options JSON with $data.xxx placeholders for dynamic data. '
             'Accepts a single options object (dict) or a list of options for multi-panel dashboards.',
    )

    # Computed text field for editing JSON in form view (ace widget doesn't work with Json field)
    chart_options_text = fields.Text(
        string='Chart Options (Text)',
        compute='_compute_chart_options_text',
        inverse='_inverse_chart_options_text',
        help='JSON text representation for editing',
    )

    # Renderer selection
    renderer = fields.Selection(
        [('canvas', 'Canvas (Default)'), ('svg', 'SVG')],
        default='canvas',
        help='Canvas for large datasets/effects, SVG for mobile/memory-sensitive',
    )

    # Responsive media queries (JSON array for ECharts native baseOption + media)
    media_queries = fields.Json(
        string='Responsive Media Queries',
        help='Array of {query: {maxWidth: N}, option: {...}} for responsive layouts using ECharts native media system',
    )

    # Computed text field for editing media_queries in form view
    media_queries_text = fields.Text(
        string='Media Queries (Text)',
        compute='_compute_media_queries_text',
        inverse='_inverse_media_queries_text',
        help='JSON text representation of media queries for editing',
    )

    @api.depends('chart_options')
    def _compute_chart_options_text(self):
        for record in self:
            if record.chart_options:
                record.chart_options_text = json.dumps(record.chart_options, indent=2)
            else:
                record.chart_options_text = ''

    def _inverse_chart_options_text(self):
        for record in self:
            if record.chart_options_text:
                try:
                    record.chart_options = json.loads(record.chart_options_text)
                except json.JSONDecodeError as e:
                    raise ValidationError(
                        f"Invalid JSON in Chart Options: {e}"
                    ) from None
            else:
                record.chart_options = False

    @api.depends('media_queries')
    def _compute_media_queries_text(self):
        for record in self:
            if record.media_queries:
                record.media_queries_text = json.dumps(record.media_queries, indent=2)
            else:
                record.media_queries_text = ''

    def _inverse_media_queries_text(self):
        for record in self:
            if record.media_queries_text:
                try:
                    record.media_queries = json.loads(record.media_queries_text)
                except json.JSONDecodeError as e:
                    raise ValidationError(
                        f"Invalid JSON in Media Queries: {e}"
                    ) from None
            else:
                record.media_queries = False

    # Advanced: Extensions and custom JavaScript
    extension_urls = fields.Text(
        string='Extension CDN URLs',
        default=DEFAULT_EXTENSION_URLS,
        help='One CDN URL per line for ECharts extensions. '
             'Default includes: echarts-gl (3D), wordcloud, liquidfill, stat. '
             'Extensions load before chart initialization.',
    )
    pre_init_js = fields.Text(
        string='Pre-Init JavaScript',
        help='JavaScript code to run BEFORE chart.setOption(). '
             'Use for: echarts.registerMap(), echarts.registerTheme(), '
             'echarts.registerTransform(), custom data preprocessing. '
             'Available variables: echarts, chartDom, data (raw result), options (parsed chart_options)',
    )
    post_init_js = fields.Text(
        string='Post-Init JavaScript',
        help='JavaScript code to run AFTER chart.setOption(). '
             'Use for: event handlers (chart.on("click", ...)), '
             'drill-down logic, export buttons, external integrations. '
             'Available variables: echarts, chart (initialized instance), chartDom, data (raw result), options',
    )

    user_id = fields.Many2one(
        'res.users',
        string='Created By',
        default=lambda self: self.env.user,
        required=True,
    )
    client_id = fields.Many2one(
        'mcp.oauth.client',
        string='MCP Client',
        ondelete='set null',
        help='OAuth client that created this chart',
    )

    # Sharing
    share_with_all_users = fields.Boolean(
        string='Share with All Users',
        default=False,
        help='Make this chart visible to all MCP users',
    )
    shared_user_ids = fields.Many2many(
        'res.users',
        'mcp_echart_shared_users_rel',
        'echart_id',
        'user_id',
        string='Shared With Users',
        help='Specific users who can view this chart',
    )
    shared_group_ids = fields.Many2many(
        'res.groups',
        'mcp_echart_shared_groups_rel',
        'echart_id',
        'group_id',
        string='Shared With Groups',
        help='Groups whose members can view this chart',
    )

    # Public link sharing (no login required)
    public_access_enabled = fields.Boolean(
        string='Public Link Enabled',
        default=False,
        help='Enable sharing via public link (no login required)',
    )
    public_access_token = fields.Char(
        string='Public Access Token',
        readonly=True,
        copy=False,
        index=True,
        help='Token for public access URL',
    )
    public_token_created_at = fields.Datetime(
        string='Public Token Created',
        readonly=True,
    )
    public_url = fields.Char(
        string='Public URL',
        compute='_compute_public_url',
        help='Shareable public link (only valid when public access is enabled)',
    )

    # Computed URL for standalone view (requires login)
    dashboard_url = fields.Char(
        string='Dashboard URL',
        compute='_compute_dashboard_url',
    )

    # Embed code for iframe embedding
    embed_code = fields.Char(
        string='Embed Code',
        compute='_compute_embed_code',
    )

    def _compute_dashboard_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for record in self:
            if record.id:
                record.dashboard_url = f"{base_url}/mcp/echart/{record.id}"
            else:
                record.dashboard_url = False

    @api.depends('public_access_enabled', 'public_url', 'dashboard_url')
    def _compute_embed_code(self):
        for record in self:
            if not record.id:
                record.embed_code = False
                continue
            url = record.public_url if record.public_access_enabled and record.public_url else record.dashboard_url
            if url:
                record.embed_code = f'<iframe src="{url}?embed=1" width="100%" height="700" style="border: none;" loading="lazy"></iframe>'
            else:
                record.embed_code = False

    @api.depends('public_access_enabled', 'public_access_token')
    def _compute_public_url(self):
        """Compute the full public URL."""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for record in self:
            if record.id and record.public_access_enabled and record.public_access_token:
                record.public_url = f"{base_url}/mcp/echart/public/{record.id}/{record.public_access_token}"
            else:
                record.public_url = False

    def fetch_chart_data(self):
        """
        Execute data_code and return result dict.

        Uses MCPExecutor from services (same as execute_orm tool).
        """
        self.ensure_one()
        if not self.data_code:
            return {}

        return MCPExecutor.execute(self.env, self.data_code, extra_locals={
            'mcp_chart_id': self.id,
        })

    @api.model
    def get_accessible_charts(self):
        """
        Get all charts accessible to the current user.

        A chart is accessible if:
        - User created it, OR
        - share_with_all_users is True, OR
        - User is in shared_user_ids, OR
        - User is in one of the shared_group_ids
        """
        user = self.env.user
        return self.search([
            '|', '|', '|',
            ('user_id', '=', user.id),
            ('share_with_all_users', '=', True),
            ('shared_user_ids', 'in', user.ids),
            ('shared_group_ids', 'in', user.groups_id.ids),
        ])

    def action_view_dashboard(self):
        """Open the dashboard in a new browser tab."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': self.dashboard_url,
            'target': 'new',
        }

    # -------------------------------------------------------------------------
    # Public Link Methods
    # -------------------------------------------------------------------------

    def write(self, vals):
        """Override write to auto-generate token when public access is enabled."""
        res = super().write(vals)
        # Auto-generate token when enabling public access without existing token
        if vals.get('public_access_enabled') and not vals.get('public_access_token'):
            for record in self:
                if record.public_access_enabled and not record.public_access_token:
                    record._generate_public_token_silent()
        return res

    def _generate_public_token_silent(self):
        """Generate token without returning notification (for auto-generation)."""
        self.ensure_one()
        token = secrets.token_urlsafe(32)
        # Use super().write to avoid recursion
        super(MCPEChart, self).write({
            'public_access_token': token,
            'public_token_created_at': fields.Datetime.now(),
        })
        return token

    def regenerate_public_token(self):
        """Regenerate the public access token, invalidating the old one."""
        self.ensure_one()
        token = secrets.token_urlsafe(32)
        self.write({
            'public_access_enabled': True,
            'public_access_token': token,
            'public_token_created_at': fields.Datetime.now(),
        })
        return True

    def disable_public_access(self):
        """Disable public access and clear the token."""
        self.ensure_one()
        self.write({
            'public_access_enabled': False,
            'public_access_token': False,
            'public_token_created_at': False,
        })

    @api.model
    def validate_public_token(self, echart_id, token):
        """
        Validate a public access token.

        :param echart_id: ID of the echart record
        :param token: The token to validate
        :return: echart record if valid, None otherwise
        """
        if not token:
            return None

        echart = self.sudo().search([
            ('id', '=', echart_id),
            ('public_access_enabled', '=', True),
            ('public_access_token', '=', token),
        ], limit=1)

        return echart if echart else None

    # -------------------------------------------------------------------------
    # Version Control
    # -------------------------------------------------------------------------
    def action_export(self):
        """Export echart as CSV with external IDs and post to chatter.

        Includes external IDs so the CSV can be reimported on the same or a
        different instance to update the existing record (matched by external
        ID) or create a new one (if the ID doesn't exist on the target).
        """
        self.ensure_one()

        fields_to_export = [
            'id', 'name', 'description', 'data_code', 'chart_options_text',
            'renderer', 'media_queries_text',
            'extension_urls', 'pre_init_js', 'post_init_js',
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
        filename = f"{self.name.replace(' ', '_')}_{timestamp}.csv"

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
            body=f"EChart exported: {filename}",
            attachment_ids=[attachment.id],
        )

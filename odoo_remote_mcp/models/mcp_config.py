# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG


class MCPConfig(models.Model):
    _name = 'mcp.config'
    _description = 'MCP Server Configuration'
    _rec_name = 'server_name'

    # -------------------------------------------------------------------------
    # Singleton Enforcement
    # -------------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        if self.search_count([]) >= 1:
            raise UserError(
                "Only one MCP configuration record can exist. "
                "Please edit the existing one."
            )
        return super().create(vals_list)

    def unlink(self):
        # Allow deletion during module uninstall
        if self.env.context.get(MODULE_UNINSTALL_FLAG):
            return super().unlink()
        raise UserError("Cannot delete the MCP configuration record.")

    @api.model
    def get_config(self):
        """Get the singleton configuration record."""
        config = self.search([], limit=1)
        if not config:
            # Should not happen if data file is loaded, but handle gracefully
            config = self.create({})
        return config

    # -------------------------------------------------------------------------
    # General Settings
    # -------------------------------------------------------------------------
    enabled = fields.Boolean(
        string='Enable MCP Server',
        default=True,
        help='Enable the Model Context Protocol server endpoint at /mcp',
    )
    server_name = fields.Char(
        string='Server Name',
        default='Odoo MCP Server',
        required=True,
        help='Name shown to MCP clients',
    )

    # -------------------------------------------------------------------------
    # OAuth Settings
    # -------------------------------------------------------------------------
    access_token_ttl = fields.Integer(
        string='Access Token TTL (seconds)',
        default=3600,
        help='How long access tokens remain valid (default: 1 hour)',
    )
    refresh_token_ttl = fields.Integer(
        string='Refresh Token TTL (seconds)',
        default=2592000,
        help='How long refresh tokens remain valid (default: 30 days)',
    )

    # -------------------------------------------------------------------------
    # Logging Settings
    # -------------------------------------------------------------------------
    enable_execution_logs = fields.Boolean(
        string='Enable Execution Logs',
        default=True,
        help='Log all MCP tool executions for audit purposes',
    )
    log_retention_days = fields.Integer(
        string='Execution Log Retention (days)',
        default=30,
        help='How long to keep execution audit logs (0 = forever)',
    )

    # -------------------------------------------------------------------------
    # User Allowlists for Write Tools
    # -------------------------------------------------------------------------
    create_record_allowed_users = fields.Many2many(
        'res.users',
        'mcp_config_create_users_rel',
        'config_id',
        'user_id',
        string='Create Record - Allowed Users',
        help='Users who can use the create_record tool',
    )
    update_record_allowed_users = fields.Many2many(
        'res.users',
        'mcp_config_update_users_rel',
        'config_id',
        'user_id',
        string='Update Record - Allowed Users',
        help='Users who can use the update_record tool',
    )
    delete_record_allowed_users = fields.Many2many(
        'res.users',
        'mcp_config_delete_users_rel',
        'config_id',
        'user_id',
        string='Delete Record - Allowed Users',
        help='Users who can use the delete_record tool',
    )
    execute_method_allowed_users = fields.Many2many(
        'res.users',
        'mcp_config_exec_method_users_rel',
        'config_id',
        'user_id',
        string='Execute Method - Allowed Users',
        help='Users who can use the execute_method tool',
    )
    execute_orm_allowed_users = fields.Many2many(
        'res.users',
        'mcp_config_exec_orm_users_rel',
        'config_id',
        'user_id',
        string='Execute ORM - Allowed Users',
        help='Users who can use the execute_orm tool',
    )
    code_access_allowed_users = fields.Many2many(
        'res.users',
        'mcp_config_code_access_users_rel',
        'config_id',
        'user_id',
        string='Code Access - Allowed Users',
        help='Users who can use the code_search and code_read tools',
    )
    create_echart_allowed_users = fields.Many2many(
        'res.users',
        'mcp_config_create_echart_users_rel',
        'config_id',
        'user_id',
        string='Create EChart - Allowed Users',
        help='Users who can create ECharts dashboards via MCP (same risk as execute_orm)',
    )
    manage_webapp_allowed_users = fields.Many2many(
        'res.users',
        'mcp_config_manage_webapp_users_rel',
        'config_id',
        'user_id',
        string='Manage WebApp - Allowed Users',
        help='Users who can create and manage React web applications via MCP (same risk as execute_orm)',
    )
    manage_module_allowed_users = fields.Many2many(
        'res.users',
        'mcp_config_manage_module_users_rel',
        'config_id',
        'user_id',
        string='Manage Module - Allowed Users',
        help='Users who can create importable data modules and install them into Odoo via MCP',
    )
    echart_expose_data = fields.Boolean(
        string='Expose EChart Data in Tool Response',
        default=True,
        help='When enabled, the create_echart tool returns the validation result data to the AI agent. '
             'Disable for data isolation - AI will only receive success/failure status and chart URL.',
    )
    module_post_zip_to_chatter = fields.Boolean(
        string='Post ZIP to Chatter on Install',
        default=True,
        help='When enabled, the module ZIP file is attached to the chatter message '
             'after each install for version history and traceability.',
    )

    # -------------------------------------------------------------------------
    # Code Access Settings
    # -------------------------------------------------------------------------
    code_search_max_matches = fields.Integer(
        string='Code Search Max Matches',
        default=500,
        help='Maximum number of matches code_search can return',
    )
    code_read_max_lines = fields.Integer(
        string='Code Read Max Lines',
        default=500,
        help='Maximum lines code_read can return in one call',
    )

    # -------------------------------------------------------------------------
    # Model Access Restrictions
    # -------------------------------------------------------------------------
    model_access_ids = fields.One2many(
        'mcp.model.access',
        'config_id',
        string='Model Access Groups',
        help='Define which models different users can access via MCP',
    )

    # -------------------------------------------------------------------------
    # Computed Fields for Smart Buttons
    # -------------------------------------------------------------------------
    oauth_client_count = fields.Integer(
        string='OAuth Clients',
        compute='_compute_oauth_client_count',
    )
    oauth_token_count = fields.Integer(
        string='Active Tokens',
        compute='_compute_oauth_token_count',
    )
    execution_log_count = fields.Integer(
        string='Execution Logs',
        compute='_compute_execution_log_count',
    )
    endpoint_url = fields.Char(
        string='MCP Endpoint URL',
        compute='_compute_endpoint_url',
    )
    mcp_user_count = fields.Integer(
        string='MCP Users',
        compute='_compute_mcp_user_count',
    )
    echart_count = fields.Integer(
        string='ECharts',
        compute='_compute_echart_count',
    )
    webapp_count = fields.Integer(
        string='WebApps',
        compute='_compute_webapp_count',
    )

    def _compute_oauth_client_count(self):
        Client = self.env['mcp.oauth.client'].sudo()
        count = Client.search_count([('active', '=', True)])
        for record in self:
            record.oauth_client_count = count

    def _compute_oauth_token_count(self):
        Token = self.env['mcp.oauth.token'].sudo()
        now = fields.Datetime.now()
        count = Token.search_count([('expires_at', '>', now)])
        for record in self:
            record.oauth_token_count = count

    def _compute_execution_log_count(self):
        Log = self.env['mcp.execution.log'].sudo()
        count = Log.search_count([])
        for record in self:
            record.execution_log_count = count

    @api.depends('enabled')
    def _compute_endpoint_url(self):
        from odoo import http

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')

        # Detect multi-db setup
        try:
            db_list = http.db_list(force=True)
            is_multi_db = len(db_list) > 1
        except Exception:
            is_multi_db = False

        current_db = self.env.cr.dbname

        for record in self:
            if base_url:
                if is_multi_db:
                    # Path-based routing for multi-db: /<db>/mcp
                    url = f"{base_url}/{current_db}/mcp"
                else:
                    url = f"{base_url}/mcp"
                record.endpoint_url = url
            else:
                record.endpoint_url = '/mcp'

    def _compute_mcp_user_count(self):
        group = self.env.ref('odoo_remote_mcp.group_mcp_user', raise_if_not_found=False)
        count = len(group.users) if group else 0
        for record in self:
            record.mcp_user_count = count

    def _compute_echart_count(self):
        EChart = self.env['mcp.echart'].sudo()
        count = EChart.search_count([])
        for record in self:
            record.echart_count = count

    def _compute_webapp_count(self):
        WebApp = self.env['mcp.webapp'].sudo()
        count = WebApp.search_count([])
        for record in self:
            record.webapp_count = count

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------
    def add_user_to_all_allowlists(self, user):
        """Add a user to all tool allowlists."""
        self.ensure_one()
        self.write({
            'create_record_allowed_users': [(4, user.id)],
            'update_record_allowed_users': [(4, user.id)],
            'delete_record_allowed_users': [(4, user.id)],
            'execute_method_allowed_users': [(4, user.id)],
            'execute_orm_allowed_users': [(4, user.id)],
            'code_access_allowed_users': [(4, user.id)],
            'create_echart_allowed_users': [(4, user.id)],
            'manage_webapp_allowed_users': [(4, user.id)],
            'manage_module_allowed_users': [(4, user.id)],
        })

    def is_user_allowed_for_tool(self, user, tool_name):
        """
        Check if a user is allowed to use a specific write tool.

        :param user: res.users record
        :param tool_name: Name of the tool
        :return: True if allowed, False otherwise
        """
        self.ensure_one()

        # Map tool names to allowlist fields
        tool_allowlist_map = {
            'create_record': 'create_record_allowed_users',
            'update_record': 'update_record_allowed_users',
            'delete_record': 'delete_record_allowed_users',
            'execute_method': 'execute_method_allowed_users',
            'execute_orm': 'execute_orm_allowed_users',
            'code_search': 'code_access_allowed_users',
            'code_read': 'code_access_allowed_users',
            'create_echart': 'create_echart_allowed_users',
            'search_webapp_code': 'manage_webapp_allowed_users',
            'search_module_code': 'manage_module_allowed_users',
            'manage_webapp': 'manage_webapp_allowed_users',
            'manage_module': 'manage_module_allowed_users',
        }

        # Read tools don't need allowlist check
        if tool_name not in tool_allowlist_map:
            return True

        allowlist_field = tool_allowlist_map[tool_name]
        allowed_users = getattr(self, allowlist_field)

        return user in allowed_users

    def get_restricted_models_for_user(self, user):
        """
        Get model restrictions for a user (blocklist approach).

        Resolution:
        1. No access configurations → None (no restrictions)
        2. User in a specific configuration → that configuration's model group
        3. User not in any configuration but default exists → default's model group
        4. User not in any configuration and no default → None (no restrictions)

        Empty model_line_ids in a model group means "no restrictions" (returns None).

        :param user: res.users record
        :return: dict mapping model_name → {read, create, write, unlink} where
                 True = operation BLOCKED, False = operation allowed,
                 or None if no restrictions
        """
        self.ensure_one()

        if not self.model_access_ids:
            return None  # No restrictions

        # Use sudo for internal security check
        access_ids = self.sudo().model_access_ids

        # Check for user-specific configuration
        user_config = access_ids.filtered(
            lambda c: user.id in c.user_ids.ids
        )
        if user_config:
            lines = user_config[0].model_group_id.model_line_ids
            # Empty model_line_ids means "no restrictions" → return None
            if not lines:
                return None
            return {
                line.model_name: {
                    'read': line.perm_read,
                    'create': line.perm_create,
                    'write': line.perm_write,
                    'unlink': line.perm_unlink,
                }
                for line in lines
            }

        # Check for default configuration (empty user_ids)
        default_config = access_ids.filtered(
            lambda c: not c.user_ids
        )
        if default_config:
            lines = default_config[0].model_group_id.model_line_ids
            # Empty model_line_ids means "no restrictions" → return None
            if not lines:
                return None
            return {
                line.model_name: {
                    'read': line.perm_read,
                    'create': line.perm_create,
                    'write': line.perm_write,
                    'unlink': line.perm_unlink,
                }
                for line in lines
            }

        # No matching configuration and no default → no restrictions
        return None

    def get_restricted_fields_for_user(self, user):
        """
        Get the set of restricted (model, field) tuples for a user.

        Resolution follows same logic as get_restricted_models_for_user.

        Empty restricted_fields in a model group means "no field restrictions" (returns None).

        :param user: res.users record
        :return: set of (model_name, field_name) tuples, or None if no restrictions
        """
        self.ensure_one()

        if not self.model_access_ids:
            return None  # No restrictions

        # Use sudo for internal security check
        access_ids = self.sudo().model_access_ids

        # Check for user-specific configuration
        user_config = access_ids.filtered(
            lambda c: user.id in c.user_ids.ids
        )
        if user_config:
            restricted = user_config[0].model_group_id.restricted_fields
            # Empty restricted_fields means "no restrictions" → return None
            return {(f.model, f.name) for f in restricted} if restricted else None

        # Check for default configuration (empty user_ids)
        default_config = access_ids.filtered(
            lambda c: not c.user_ids
        )
        if default_config:
            restricted = default_config[0].model_group_id.restricted_fields
            # Empty restricted_fields means "no restrictions" → return None
            return {(f.model, f.name) for f in restricted} if restricted else None

        # No matching configuration and no default → no restrictions
        return None

    # -------------------------------------------------------------------------
    # Smart Button Actions
    # -------------------------------------------------------------------------
    def action_view_oauth_clients(self):
        """Open OAuth clients list."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'OAuth Clients',
            'res_model': 'mcp.oauth.client',
            'view_mode': 'tree,form',
            'target': 'current',
            'context': {'default_active': True},
        }

    def action_view_oauth_tokens(self):
        """Open OAuth tokens list."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Access Tokens',
            'res_model': 'mcp.oauth.token',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [('expires_at', '>', fields.Datetime.now())],
        }

    def action_view_execution_logs(self):
        """Open execution logs list."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Execution Logs',
            'res_model': 'mcp.execution.log',
            'view_mode': 'tree,form',
            'target': 'current',
        }

    def action_view_mcp_users(self):
        """Open list of users with MCP User group (explicit or implied)."""
        self.ensure_one()
        group = self.env.ref('odoo_remote_mcp.group_mcp_user', raise_if_not_found=False)
        return {
            'type': 'ir.actions.act_window',
            'name': 'MCP Users',
            'res_model': 'res.users',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [('groups_id', 'in', group.ids)] if group else [('id', '=', False)],
            'context': {'create': False},
        }

    def action_cleanup_expired_tokens(self):
        """Manually trigger token cleanup."""
        self.env['mcp.oauth.token'].sudo()._cleanup_expired_tokens()
        self.env['mcp.oauth.code'].sudo()._cleanup_expired_codes()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Cleanup Complete',
                'message': 'Expired tokens and authorization codes have been removed.',
                'type': 'success',
            }
        }

    def action_view_echarts(self):
        """Open ECharts list."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'ECharts Dashboards',
            'res_model': 'mcp.echart',
            'view_mode': 'tree,form',
            'target': 'current',
        }

    def action_view_webapps(self):
        """Open WebApps list."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Web Applications',
            'res_model': 'mcp.webapp',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
        }

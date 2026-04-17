# -*- coding: utf-8 -*-
from datetime import timedelta
from odoo import api, fields, models


class MCPExecutionLog(models.Model):
    _name = 'mcp.execution.log'
    _description = 'MCP Code Execution Audit Log'
    _rec_name = 'create_date'
    _order = 'create_date desc'

    user_id = fields.Many2one(
        'res.users',
        string='User',
        required=True,
        ondelete='cascade',
        index=True,
    )
    client_id = fields.Many2one(
        'mcp.oauth.client',
        string='OAuth Client',
        ondelete='set null',
        index=True,
    )
    api_key_id = fields.Many2one(
        'mcp.api.key',
        string='API Key',
        ondelete='set null',
        index=True,
    )
    tool_name = fields.Char(
        string='Tool Name',
        required=True,
        index=True,
    )
    code = fields.Text(
        string='Code Executed',
        help='The code that was executed (for execute_orm)',
    )
    parameters = fields.Text(
        string='Parameters',
        help='JSON string of parameters passed to the tool',
    )
    result = fields.Text(
        string='Result',
        help='The result returned by the tool',
    )
    error = fields.Text(
        string='Error',
        help='Error message if execution failed',
    )
    success = fields.Boolean(
        string='Success',
        default=True,
    )
    execution_time = fields.Float(
        string='Execution Time (s)',
        digits=(16, 4),
    )
    ip_address = fields.Char(
        string='IP Address',
    )

    @api.model
    def log_execution(self, user_id, client_id, tool_name, code=None, parameters=None,
                      result=None, error=None, success=True, execution_time=0.0, ip_address=None,
                      api_key_id=None):
        """
        Log a tool execution.

        :param user_id: ID of the user
        :param client_id: ID of the OAuth client (optional)
        :param tool_name: Name of the tool executed
        :param code: Code executed (for execute_orm)
        :param parameters: Parameters as JSON string
        :param result: Result as string (truncated)
        :param error: Error message if any
        :param success: Whether execution succeeded
        :param execution_time: Time taken in seconds
        :param ip_address: Client IP address
        :return: Created log record or False if logging disabled
        """
        # Check if execution logging is enabled
        config = self.env['mcp.config'].sudo().get_config()
        if not config.enable_execution_logs:
            return False
        # Truncate very long strings
        max_length = 100000
        if result and len(result) > max_length:
            result = result[:max_length] + '\n... (truncated)'
        if parameters and len(parameters) > max_length:
            parameters = parameters[:max_length] + '\n... (truncated)'

        # Use sudo to ensure logging always works regardless of MCP model restrictions
        return self.sudo().create({
            'user_id': user_id,
            'client_id': client_id,
            'api_key_id': api_key_id,
            'tool_name': tool_name,
            'code': code,
            'parameters': parameters,
            'result': result,
            'error': error,
            'success': success,
            'execution_time': execution_time,
            'ip_address': ip_address,
        })

    @api.model
    def _cleanup_old_logs(self):
        """Cron job to clean up old execution logs."""
        config = self.env['mcp.config'].sudo().get_config()
        retention_days = config.log_retention_days or 30

        if retention_days <= 0:
            return True

        cutoff = fields.Datetime.now() - timedelta(days=retention_days)
        old_logs = self.search([('create_date', '<', cutoff)])
        old_logs.unlink()
        return True

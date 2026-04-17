# -*- coding: utf-8 -*-
import re
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class MCPPromptArgument(models.Model):
    """
    MCP Prompt Argument.

    Defines an argument that can be passed to a prompt template.
    Per MCP spec: https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
    """
    _name = 'mcp.prompt.argument'
    _description = 'MCP Prompt Argument'
    _order = 'sequence, id'

    prompt_id = fields.Many2one(
        'mcp.prompt',
        string='Prompt',
        required=True,
        ondelete='cascade',
        index=True,
    )
    name = fields.Char(
        string='Name',
        required=True,
        help='Argument identifier used in template placeholders (e.g., {{name}})',
    )
    description = fields.Char(
        string='Description',
        help='Description of what this argument is for',
    )
    required = fields.Boolean(
        string='Required',
        default=False,
        help='Whether this argument must be provided',
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Order in which arguments appear',
    )

    _sql_constraints = [
        ('unique_name_per_prompt', 'UNIQUE(prompt_id, name)', 'Argument names must be unique within a prompt'),
    ]

    def get_mcp_format(self):
        """Return argument in MCP spec format."""
        self.ensure_one()
        result = {
            'name': self.name,
        }
        if self.description:
            result['description'] = self.description
        if self.required:
            result['required'] = True
        return result


class MCPPrompt(models.Model):
    """
    MCP Prompt.

    Reusable prompt templates that appear as slash commands in MCP clients.
    Per MCP spec: https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
    """
    _name = 'mcp.prompt'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'MCP Prompt'
    _order = 'sequence, name'

    name = fields.Char(
        string='Name',
        required=True,
        index=True,
        tracking=True,
        help='Unique identifier for the prompt (used in MCP prompts/get)',
    )
    title = fields.Char(
        string='Title',
        help='Human-readable display name shown in MCP clients',
    )
    description = fields.Text(
        string='Description',
        tracking=True,
        required=True,
        help='Description shown to users in MCP clients',
    )
    argument_ids = fields.One2many(
        'mcp.prompt.argument',
        'prompt_id',
        string='Arguments',
        help='Arguments that can be passed to this prompt',
    )
    template = fields.Text(
        string='Template',
        required=True,
        help='Prompt template text. Use {{argument_name}} placeholders for argument substitution.',
    )
    expose_to_mcp_client = fields.Boolean(
        string='Expose to MCP Client',
        default=True,
        tracking=True,
        help='If enabled, this prompt will be available via prompts/list',
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Uncheck to archive the prompt',
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Order in which prompts appear in the list',
    )

    # Ownership and sharing fields
    owner_id = fields.Many2one(
        'res.users',
        string='Owner',
        default=lambda self: self.env.user,
        required=True,
        tracking=True,
        help='The user who owns this prompt template',
    )
    share_with_all_users = fields.Boolean(
        string='Share with All Users',
        default=False,
        tracking=True,
        help='If enabled, all MCP users can see and use this prompt. '
             'If disabled, only the owner and explicitly shared users can access it.',
    )
    shared_user_ids = fields.Many2many(
        'res.users',
        'mcp_prompt_shared_users_rel',
        'prompt_id',
        'user_id',
        string='Shared With Users',
        help='Specific users who can access this prompt (in addition to the owner). '
             'Only applies when "Share with All Users" is disabled.',
    )

    _sql_constraints = [
        ('unique_name', 'UNIQUE(name)', 'Prompt names must be unique'),
    ]

    @api.constrains('name')
    def _check_name_format(self):
        """Ensure prompt name is a valid identifier."""
        for record in self:
            if record.name and not re.match(r'^[a-z][a-z0-9_]*$', record.name):
                raise ValidationError(
                    "Prompt name must start with a lowercase letter and contain "
                    "only lowercase letters, numbers, and underscores."
                )

    def _is_visible_to_user(self, user):
        """
        Check if this prompt is visible to the given user.

        A prompt is visible if:
        - User is the owner, OR
        - share_with_all_users is True, OR
        - User is in shared_user_ids

        :param user: res.users record
        :return: Boolean
        """
        self.ensure_one()
        if self.owner_id.id == user.id:
            return True
        if self.share_with_all_users:
            return True
        if user.id in self.shared_user_ids.ids:
            return True
        return False

    @api.model
    def _get_visible_domain(self, user=None):
        """
        Get domain filter for prompts visible to a user.

        :param user: res.users record (defaults to current user)
        :return: Domain list
        """
        if user is None:
            user = self.env.user
        return [
            '|', '|',
            ('owner_id', '=', user.id),
            ('share_with_all_users', '=', True),
            ('shared_user_ids', 'in', [user.id]),
        ]

    @api.model
    def get_prompts_for_mcp(self, user=None):
        """
        Get all prompts visible to a user for MCP prompts/list.

        :param user: res.users record (defaults to current user)
        :return: Recordset of mcp.prompt
        """
        if user is None:
            user = self.env.user
        domain = [
            ('active', '=', True),
            ('expose_to_mcp_client', '=', True),
        ] + self._get_visible_domain(user)
        return self.search(domain, order='sequence, name')

    def get_mcp_format(self):
        """
        Return prompt in MCP spec format for prompts/list.

        Returns a dictionary with:
        - name: Unique identifier (required)
        - title: Display name (optional)
        - description: Description (optional)
        - arguments: List of argument definitions (optional)
        """
        self.ensure_one()
        result = {
            'name': self.name,
        }
        if self.title:
            result['title'] = self.title
        if self.description:
            result['description'] = self.description
        if self.argument_ids:
            result['arguments'] = [arg.get_mcp_format() for arg in self.argument_ids]
        return result

    def get_prompt_message(self, arguments=None):
        """
        Get the prompt with arguments substituted.

        Per MCP spec, prompts/get returns:
        - description: Optional description
        - messages: Array of PromptMessage objects

        :param arguments: Dictionary of argument name -> value
        :return: Dictionary with description and messages
        """
        self.ensure_one()
        arguments = arguments or {}

        # Validate required arguments
        for arg in self.argument_ids.filtered('required'):
            if arg.name not in arguments:
                raise ValidationError(f"Missing required argument: {arg.name}")

        # Substitute placeholders in template
        text = self.template
        for arg in self.argument_ids:
            placeholder = '{{' + arg.name + '}}'
            value = arguments.get(arg.name, '')
            text = text.replace(placeholder, str(value) if value else '')

        # Build response
        result = {
            'messages': [
                {
                    'role': 'user',
                    'content': {
                        'type': 'text',
                        'text': text,
                    }
                }
            ]
        }
        if self.description:
            result['description'] = self.description

        return result

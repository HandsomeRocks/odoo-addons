# -*- coding: utf-8 -*-
import hashlib
import json
import secrets
import uuid
from odoo import api, fields, models


class MCPOAuthClient(models.Model):
    _name = 'mcp.oauth.client'
    _description = 'MCP OAuth Client (DCR)'
    _rec_name = 'client_id'
    _order = 'registered_at desc'

    client_id = fields.Char(
        string='Client ID',
        required=True,
        index=True,
        readonly=True,
        default=lambda self: str(uuid.uuid4()),
        copy=False,
    )
    client_secret = fields.Char(
        string='Client Secret',
        readonly=True,
        copy=False,
        groups='odoo_remote_mcp.group_mcp_admin',
        help='Only shown once during registration. Stored as hash.',
    )
    client_secret_hash = fields.Char(
        string='Client Secret Hash',
        readonly=True,
        copy=False,
    )
    client_name = fields.Char(
        string='Client Name',
        required=True,
    )
    client_type = fields.Selection([
        ('public', 'Public'),
        ('confidential', 'Confidential'),
    ], string='Client Type', default='public', required=True)

    redirect_uris = fields.Text(
        string='Redirect URIs',
        help='JSON array of allowed redirect URIs',
    )
    redirect_uris_display = fields.Text(
        string='Allowed Redirect URIs',
        compute='_compute_redirect_uris_display',
    )
    grant_types = fields.Char(
        string='Grant Types',
        default='authorization_code,refresh_token',
    )
    scope = fields.Char(
        string='Scope',
        default='odoo.read odoo.write odoo.execute offline_access',
    )

    token_endpoint_auth_method = fields.Selection([
        ('none', 'None (Public)'),
        ('client_secret_post', 'Client Secret Post'),
        ('client_secret_basic', 'Client Secret Basic'),
    ], string='Token Endpoint Auth Method', default='none')

    registered_at = fields.Datetime(
        string='Registered At',
        default=fields.Datetime.now,
        readonly=True,
    )
    active = fields.Boolean(default=True)

    # Relationship to tokens (for cleanup)
    token_ids = fields.One2many(
        'mcp.oauth.token', 'client_id', string='Access Tokens')
    code_ids = fields.One2many(
        'mcp.oauth.code', 'client_id', string='Authorization Codes')

    user_ids = fields.Many2many(
        'res.users',
        string='Users',
        compute='_compute_user_ids',
        readonly=True,
        store=False,  # Not stored, computed on the fly
        help="Unique users who have authorized this client."
    )

    @api.depends('token_ids.user_id')
    def _compute_user_ids(self):
        for client in self:
            client.user_ids = client.token_ids.mapped('user_id')

    @api.depends('redirect_uris')
    def _compute_redirect_uris_display(self):
        for record in self:
            if record.redirect_uris:
                try:
                    uris = json.loads(record.redirect_uris)
                    record.redirect_uris_display = '\n'.join(uris)
                except (json.JSONDecodeError, TypeError):
                    record.redirect_uris_display = record.redirect_uris
            else:
                record.redirect_uris_display = ''

    @api.model
    def _hash_secret(self, secret):
        """Hash a client secret using SHA-256."""
        return hashlib.sha256(secret.encode()).hexdigest()

    def verify_secret(self, secret):
        """Verify a client secret against the stored hash."""
        self.ensure_one()
        if not self.client_secret_hash:
            return False
        return secrets.compare_digest(
            self._hash_secret(secret),
            self.client_secret_hash
        )

    def get_redirect_uris(self):
        """Get redirect URIs as a list."""
        self.ensure_one()
        if not self.redirect_uris:
            return []
        try:
            return json.loads(self.redirect_uris)
        except (json.JSONDecodeError, TypeError):
            return [self.redirect_uris]

    def is_redirect_uri_valid(self, redirect_uri):
        """Check if a redirect URI is allowed for this client."""
        self.ensure_one()
        allowed_uris = self.get_redirect_uris()
        return redirect_uri in allowed_uris

    @api.model
    def register_client(self, client_data):
        """
        Dynamic Client Registration (RFC 7591).

        :param client_data: Dictionary with client registration data
        :return: Dictionary with registered client info
        """
        # Validate required fields
        redirect_uris = client_data.get('redirect_uris', [])
        if not redirect_uris:
            raise ValueError('redirect_uris is required')

        client_name = client_data.get('client_name', 'Unknown Client')
        grant_types = client_data.get('grant_types', ['authorization_code', 'refresh_token'])
        token_endpoint_auth_method = client_data.get('token_endpoint_auth_method', 'none')
        # Default to all scopes if not specified - users control access via tool allowlists
        from ..services.oauth_provider import OAuthProviderService
        default_scope = ' '.join(OAuthProviderService.VALID_SCOPES)
        scope = client_data.get('scope', default_scope)

        # Determine client type
        client_type = 'public' if token_endpoint_auth_method == 'none' else 'confidential'

        # Generate client credentials
        client_id = str(uuid.uuid4())
        client_secret = None
        client_secret_hash = None

        if client_type == 'confidential':
            client_secret = secrets.token_urlsafe(32)
            client_secret_hash = self._hash_secret(client_secret)

        # Create client record
        client = self.create({
            'client_id': client_id,
            'client_secret': client_secret,  # Will be shown once then cleared
            'client_secret_hash': client_secret_hash,
            'client_name': client_name,
            'client_type': client_type,
            'redirect_uris': json.dumps(redirect_uris),
            'grant_types': ','.join(grant_types) if isinstance(grant_types, list) else grant_types,
            'scope': scope if isinstance(scope, str) else ' '.join(scope),
            'token_endpoint_auth_method': token_endpoint_auth_method,
        })

        # Build response per RFC 7591
        response = {
            'client_id': client_id,
            'client_id_issued_at': int(client.registered_at.timestamp()),
            'redirect_uris': redirect_uris,
            'client_name': client_name,
            'grant_types': grant_types if isinstance(grant_types, list) else grant_types.split(','),
            'token_endpoint_auth_method': token_endpoint_auth_method,
            'scope': client.scope,
        }

        if client_secret:
            response['client_secret'] = client_secret
            # Clear the plain secret from DB after response is built
            client.write({'client_secret': False})

        return response

    def action_revoke_tokens(self):
        """Revoke all tokens for this client."""
        self.ensure_one()
        self.token_ids.unlink()
        self.code_ids.filtered(lambda c: not c.used).unlink()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Tokens Revoked',
                'message': f'All tokens for client {self.client_name} have been revoked.',
                'type': 'success',
            }
        }

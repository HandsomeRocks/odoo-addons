# -*- coding: utf-8 -*-
import hashlib
import secrets
from datetime import timedelta
from odoo import api, fields, models


class MCPOAuthToken(models.Model):
    _name = 'mcp.oauth.token'
    _description = 'MCP OAuth Token'
    _rec_name = 'access_token_prefix'
    _order = 'create_date desc'

    access_token_hash = fields.Char(
        string='Access Token Hash',
        required=True,
        index=True,
        readonly=True,
    )
    access_token_prefix = fields.Char(
        string='Access Token Prefix',
        readonly=True,
        help='First 8 characters of the token for identification',
    )
    refresh_token_hash = fields.Char(
        string='Refresh Token Hash',
        index=True,
        readonly=True,
    )
    client_id = fields.Many2one(
        'mcp.oauth.client',
        string='Client',
        required=True,
        ondelete='cascade',
        index=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='User',
        required=True,
        ondelete='cascade',
        index=True,
    )
    scope = fields.Char(string='Scope')
    expires_at = fields.Datetime(
        string='Expires At',
        required=True,
        index=True,
    )
    refresh_expires_at = fields.Datetime(
        string='Refresh Token Expires At',
    )
    resource = fields.Char(
        string='Resource',
        help='The MCP server URL this token is bound to',
    )
    is_expired = fields.Boolean(
        string='Is Expired',
        compute='_compute_is_expired',
        store=False,
    )

    @api.depends('expires_at')
    def _compute_is_expired(self):
        now = fields.Datetime.now()
        for record in self:
            record.is_expired = record.expires_at < now

    @api.model
    def _hash_token(self, token):
        """Hash a token using SHA-256."""
        return hashlib.sha256(token.encode()).hexdigest()

    @api.model
    def create_tokens(self, client, user, scope, resource=None, refresh_expires_at=None):
        """
        Create access and refresh tokens for a client/user pair.

        :param client: mcp.oauth.client record
        :param user: res.users record
        :param scope: Space-separated scope string
        :param resource: Optional resource binding
        :param refresh_expires_at: Optional absolute refresh token expiry
                                   (used during refresh to preserve original expiry)
        :return: Dictionary with tokens and metadata
        """
        config = self.env['mcp.config'].sudo().get_config()
        access_ttl = config.access_token_ttl or 3600
        refresh_ttl = config.refresh_token_ttl or 2592000

        now = fields.Datetime.now()
        expires_at = now + timedelta(seconds=access_ttl)

        # Use provided refresh_expires_at (for refresh rotation) or calculate new one
        if refresh_expires_at is None:
            refresh_expires_at = now + timedelta(seconds=refresh_ttl)

        # Generate tokens
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)

        # Create token record
        self.create({
            'access_token_hash': self._hash_token(access_token),
            'access_token_prefix': access_token[:8],
            'refresh_token_hash': self._hash_token(refresh_token),
            'client_id': client.id,
            'user_id': user.id,
            'scope': scope,
            'expires_at': expires_at,
            'refresh_expires_at': refresh_expires_at,
            'resource': resource,
        })

        return {
            'access_token': access_token,
            'token_type': 'Bearer',
            'expires_in': access_ttl,
            'refresh_token': refresh_token,
            'scope': scope,
        }

    @api.model
    def validate_access_token(self, token):
        """
        Validate an access token and return user info if valid.

        :param token: The access token string
        :return: Dictionary with user_id and scope, or None if invalid
        """
        token_hash = self._hash_token(token)
        now = fields.Datetime.now()

        token_record = self.search([
            ('access_token_hash', '=', token_hash),
            ('expires_at', '>', now),
        ], limit=1)

        if not token_record:
            return None

        if not token_record.client_id.active:
            return None

        return {
            'user_id': token_record.user_id.id,
            'scope': token_record.scope,
            'client_id': token_record.client_id.id,
            'client_name': token_record.client_id.client_name,
        }

    @api.model
    def refresh_tokens(self, refresh_token, client):
        """
        Refresh an access token using a refresh token.

        Implements refresh token rotation: old token is invalidated and new
        tokens are issued. The original refresh_expires_at is preserved to
        enforce absolute session lifetime.

        :param refresh_token: The refresh token string
        :param client: mcp.oauth.client record
        :return: Dictionary with new tokens, or None if invalid
        """
        refresh_hash = self._hash_token(refresh_token)
        now = fields.Datetime.now()

        token_record = self.search([
            ('refresh_token_hash', '=', refresh_hash),
            ('client_id', '=', client.id),
            ('refresh_expires_at', '>', now),
        ], limit=1)

        if not token_record:
            return None

        # Preserve original values from old token
        user = token_record.user_id
        scope = token_record.scope
        resource = token_record.resource
        original_refresh_expires_at = token_record.refresh_expires_at

        # Delete old token (rotation)
        token_record.unlink()

        # Create new tokens, preserving original refresh expiry
        return self.create_tokens(
            client, user, scope, resource,
            refresh_expires_at=original_refresh_expires_at
        )

    @api.model
    def revoke_token(self, token, token_type_hint=None):
        """
        Revoke a token (either access or refresh).

        :param token: The token string
        :param token_type_hint: Optional hint ('access_token' or 'refresh_token')
        :return: True if token was revoked
        """
        token_hash = self._hash_token(token)

        # Try access token first (or based on hint)
        if token_type_hint != 'refresh_token':
            token_record = self.search([
                ('access_token_hash', '=', token_hash),
            ], limit=1)
            if token_record:
                token_record.unlink()
                return True

        # Try refresh token
        if token_type_hint != 'access_token':
            token_record = self.search([
                ('refresh_token_hash', '=', token_hash),
            ], limit=1)
            if token_record:
                token_record.unlink()
                return True

        return False

    @api.model
    def _cleanup_expired_tokens(self):
        """
        Cron job to clean up expired tokens.

        Only deletes token records when the refresh token has expired.
        Access tokens may expire but the record should persist as long as
        the refresh token is valid (allowing clients to refresh).
        """
        now = fields.Datetime.now()
        # Only delete when refresh token is expired (it outlives access token)
        expired = self.search([
            ('refresh_expires_at', '<', now),
        ])
        expired.unlink()
        return True

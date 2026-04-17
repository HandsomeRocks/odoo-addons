# -*- coding: utf-8 -*-
import base64
import hashlib
import logging
import secrets
from datetime import timedelta
from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class MCPOAuthCode(models.Model):
    _name = 'mcp.oauth.code'
    _description = 'MCP OAuth Authorization Code'
    _rec_name = 'code_prefix'
    _order = 'create_date desc'

    code_hash = fields.Char(
        string='Code Hash',
        required=True,
        index=True,
        readonly=True,
    )
    code_prefix = fields.Char(
        string='Code Prefix',
        readonly=True,
        help='First 8 characters of the code for identification',
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
    redirect_uri = fields.Char(
        string='Redirect URI',
        required=True,
    )
    scope = fields.Char(string='Scope')
    expires_at = fields.Datetime(
        string='Expires At',
        required=True,
        index=True,
    )

    # PKCE fields (required for OAuth 2.1)
    code_challenge = fields.Char(
        string='Code Challenge',
        required=True,
    )
    code_challenge_method = fields.Selection([
        ('S256', 'SHA-256'),
        ('plain', 'Plain'),
    ], string='Code Challenge Method', default='S256', required=True)

    used = fields.Boolean(
        string='Used',
        default=False,
        help='Authorization codes are single-use',
    )
    state = fields.Char(
        string='State',
        help='Optional state parameter from the authorization request',
    )
    resource = fields.Char(
        string='Resource',
        help='RFC 8707 resource indicator - the MCP server URL this code is bound to',
    )

    @api.model
    def _hash_code(self, code):
        """Hash an authorization code using SHA-256."""
        return hashlib.sha256(code.encode()).hexdigest()

    @api.model
    def generate_code(self, client, user, redirect_uri, scope, code_challenge, code_challenge_method='S256', state=None, resource=None):
        """
        Generate an authorization code.

        :param client: mcp.oauth.client record
        :param user: res.users record
        :param redirect_uri: The redirect URI
        :param scope: Space-separated scope string
        :param code_challenge: PKCE code challenge
        :param code_challenge_method: PKCE method (S256 or plain)
        :param state: Optional state parameter
        :param resource: RFC 8707 resource indicator (MCP server URL)
        :return: The authorization code string
        """
        # Authorization codes expire after 10 minutes
        now = fields.Datetime.now()
        expires_at = now + timedelta(minutes=10)

        # Generate code
        code = secrets.token_urlsafe(32)

        # Create code record
        self.create({
            'code_hash': self._hash_code(code),
            'code_prefix': code[:8],
            'client_id': client.id,
            'user_id': user.id,
            'redirect_uri': redirect_uri,
            'scope': scope,
            'expires_at': expires_at,
            'code_challenge': code_challenge,
            'code_challenge_method': code_challenge_method,
            'state': state,
            'resource': resource,
        })

        return code

    @api.model
    def _verify_pkce(self, code_verifier, code_challenge, method):
        """
        Verify PKCE code verifier against challenge.

        :param code_verifier: The code verifier from token request
        :param code_challenge: The code challenge from authorization
        :param method: S256 or plain
        :return: True if valid
        """
        if method == 'plain':
            return secrets.compare_digest(code_verifier, code_challenge)
        elif method == 'S256':
            # code_challenge = BASE64URL(SHA256(code_verifier))
            digest = hashlib.sha256(code_verifier.encode()).digest()
            computed_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
            return secrets.compare_digest(computed_challenge, code_challenge)
        return False

    @api.model
    def exchange_code(self, code, code_verifier, client, redirect_uri, resource=None):
        """
        Exchange an authorization code for tokens.

        :param code: The authorization code string
        :param code_verifier: PKCE code verifier
        :param client: mcp.oauth.client record
        :param redirect_uri: The redirect URI (must match original)
        :param resource: RFC 8707 resource indicator (must match original if provided)
        :return: Token dictionary from mcp.oauth.token.create_tokens, or None
        """
        code_hash = self._hash_code(code)
        now = fields.Datetime.now()

        _logger.info("Code exchange - Looking for code hash: %s..., client_id: %s, redirect_uri: %s, resource: %s",
                     code_hash[:20], client.id, redirect_uri, resource)

        # Find and validate code
        code_record = self.search([
            ('code_hash', '=', code_hash),
            ('client_id', '=', client.id),
            ('redirect_uri', '=', redirect_uri),
            ('expires_at', '>', now),
            ('used', '=', False),
        ], limit=1)

        if not code_record:
            # Debug: check what codes exist
            all_codes = self.search([('client_id', '=', client.id), ('used', '=', False)])
            _logger.warning("Code exchange - Code not found! Available codes for client: %s",
                           [(c.code_prefix, c.redirect_uri, c.expires_at) for c in all_codes])
            return None

        _logger.info("Code exchange - Found code record: %s, challenge method: %s",
                     code_record.code_prefix, code_record.code_challenge_method)

        # Verify resource parameter matches (RFC 8707 audience binding)
        # If client sends a resource, it must match the stored one
        # If client doesn't send a resource, that's ok - we use the stored one (token will still be bound)
        if resource and code_record.resource and resource != code_record.resource:
            _logger.warning("Code exchange - Resource mismatch! expected: %s, got: %s",
                           code_record.resource, resource)
            code_record.write({'used': True})
            return None

        # Verify PKCE
        if not self._verify_pkce(code_verifier, code_record.code_challenge, code_record.code_challenge_method):
            _logger.warning("Code exchange - PKCE verification failed! verifier: %s..., challenge: %s...",
                           code_verifier[:20] if code_verifier else None,
                           code_record.code_challenge[:20] if code_record.code_challenge else None)
            # Mark as used to prevent further attempts
            code_record.write({'used': True})
            return None

        _logger.info("Code exchange - PKCE verified successfully")

        # Mark code as used
        code_record.write({'used': True})

        # Create tokens
        Token = self.env['mcp.oauth.token']
        return Token.create_tokens(
            client=client,
            user=code_record.user_id,
            scope=code_record.scope,
            resource=code_record.resource,
        )

    @api.model
    def _cleanup_expired_codes(self):
        """Cron job to clean up expired and used codes."""
        now = fields.Datetime.now()
        # Delete codes expired more than 1 hour ago (keep for audit briefly)
        cleanup_time = now - timedelta(hours=1)
        expired = self.search([
            '|',
            ('expires_at', '<', cleanup_time),
            '&', ('used', '=', True), ('expires_at', '<', now),
        ])
        expired.unlink()
        return True

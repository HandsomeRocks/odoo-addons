# -*- coding: utf-8 -*-
"""Binary data utilities for MCP resource handling."""

import base64
import logging

from odoo.tools.mimetypes import guess_mimetype

_logger = logging.getLogger(__name__)


def detect_mimetype(data):
    """
    Detect MIME type for binary data using Odoo's guess_mimetype.

    Inspects the actual binary content (magic bytes) to determine the real
    MIME type, rather than relying on field names or assumptions.

    :param data: Binary data (bytes or base64 string)
    :return: MIME type string (defaults to 'application/octet-stream')
    """
    if not data:
        return 'application/octet-stream'

    # Decode if base64 string
    if isinstance(data, str):
        try:
            raw = base64.b64decode(data)
        except Exception:
            return 'application/octet-stream'
    else:
        raw = data

    # Use Odoo's guess_mimetype which inspects magic bytes
    return guess_mimetype(raw) or 'application/octet-stream'


def is_text_mimetype(mimetype):
    """
    Determine if a MIME type likely represents text content.

    Uses MIME type structure per RFC 6838/6839:
    - text/* types are always text
    - Structured syntax suffixes (+json, +xml, etc.) indicate text
    - Binary top-level types (image, video, audio, font) are never text

    For ambiguous application/* types, caller should try UTF-8 decode
    and fall back to binary, so false negatives here are safe.

    :param mimetype: MIME type string
    :return: True if likely text content
    """
    if not mimetype:
        return False

    main_type, _, subtype = mimetype.partition('/')

    # Definite text
    if main_type == 'text':
        return True

    # Definite binary
    if main_type in ('image', 'video', 'audio', 'font'):
        return False

    # RFC 6839 structured syntax suffixes indicate text-based formats
    if any(subtype.endswith(s) for s in ('+json', '+xml', '+yaml', '+html')):
        return True

    # Common text-based application types
    return subtype in ('json', 'xml', 'javascript', 'ecmascript', 'sql', 'yaml', 'toml', 'graphql')


def _decode_binary_data(data):
    """
    Normalize binary data to (raw_bytes, base64_string) tuple.

    Odoo binary fields can return base64-encoded data as either str or bytes.
    This function handles both cases, plus raw bytes for non-Odoo sources.

    :param data: Binary data (base64 str, base64 bytes, or raw bytes)
    :return: Tuple of (raw_bytes, base64_string), or (None, None) on failure
    """
    if not data:
        return None, None

    # String input - always base64 encoded
    if isinstance(data, str):
        try:
            raw = base64.b64decode(data)
            return raw, data
        except Exception:
            return None, None

    # Bytes input - could be base64-encoded or raw
    if isinstance(data, (bytes, memoryview)):
        if isinstance(data, memoryview):
            data = bytes(data)
        try:
            # Try base64 decode first (Odoo binary fields are base64-encoded)
            raw = base64.b64decode(data)
            return raw, data.decode('ascii')
        except Exception:
            # Not valid base64, treat as raw bytes
            return data, base64.b64encode(data).decode('ascii')

    return None, None


def binary_to_resource_content(data, uri, mimetype=None):
    """
    Convert binary data to MCP resource content format.

    Handles:
    - Empty data: Returns proper MCP content with empty text/blob
    - Base64 input: Both str and bytes (Odoo binary fields can return either)
    - Text detection: If mimetype suggests text, tries UTF-8 decode
    - Binary fallback: Returns base64 blob for binary content

    :param data: Binary data (base64 str, base64 bytes, or raw bytes), or None/empty
    :param uri: Resource URI for this content
    :param mimetype: MIME type (auto-detected if None and data present)
    :return: MCP resource content dict with uri, mimeType, and text/blob
    """
    # Handle empty data - return valid MCP content with empty value
    if not data:
        mimetype = mimetype or 'application/octet-stream'
        content_field = 'text' if is_text_mimetype(mimetype) else 'blob'
        return {
            'uri': uri,
            'mimeType': mimetype,
            content_field: '',
        }

    # Normalize to raw bytes and base64 string
    # Odoo binary fields return base64-encoded data (as str OR bytes)
    raw_data, b64_data = _decode_binary_data(data)

    if raw_data is None:
        # Decoding failed - return empty
        mimetype = mimetype or 'application/octet-stream'
        return {
            'uri': uri,
            'mimeType': mimetype,
            'blob': '',
        }

    # Detect mimetype if not provided
    if not mimetype:
        mimetype = detect_mimetype(raw_data)

    # Try text decode if mimetype suggests text content
    if is_text_mimetype(mimetype):
        try:
            text_content = raw_data.decode('utf-8')
            return {
                'uri': uri,
                'mimeType': mimetype,
                'text': text_content,
            }
        except UnicodeDecodeError:
            # Not valid UTF-8, fall through to binary
            pass

    # Binary content
    return {
        'uri': uri,
        'mimeType': mimetype,
        'blob': b64_data,
    }


def attachment_to_resource_content(attachment, uri):
    """
    Convert an ir.attachment record to MCP resource content format.

    Handles URL type attachments specially, delegates all binary/text
    handling (including empty data) to binary_to_resource_content.

    :param attachment: ir.attachment record
    :param uri: Resource URI for this attachment
    :return: MCP resource content dict with uri, mimeType, and text/blob
    """
    # Handle URL type attachments (only ir.attachment can be URL type)
    if attachment.type == 'url':
        return {
            'uri': uri,
            'mimeType': 'text/uri-list',
            'text': attachment.url or '',
        }

    # Delegate to shared binary handling (handles empty, text detection, etc.)
    return binary_to_resource_content(
        attachment.datas,
        uri,
        mimetype=attachment.mimetype
    )


def fetch_field_resource_content(env, model_name, field_name, record_id, uri):
    """
    Fetch binary field data and return as MCP resource content format.

    Handles three cases:
    - ir.attachment model: Use record directly as attachment (has mimetype)
    - Attachment-backed fields: Fetch from ir.attachment by res_model/res_field/res_id
    - Direct binary fields: Read from field, detect mimetype from content

    :param env: Odoo environment
    :param model_name: Model name (e.g., 'res.partner')
    :param field_name: Binary field name
    :param record_id: Record ID
    :param uri: Resource URI for this content
    :return: MCP resource content dict, or None if no data
    """
    if model_name not in env:
        return None

    Model = env[model_name]
    field = Model._fields.get(field_name)

    if not field or field.type not in ('binary', 'image'):
        return None

    # Special case: ir.attachment itself - use record as the attachment
    # This ensures we use the stored mimetype rather than detecting
    if model_name == 'ir.attachment':
        attachment = Model.browse(record_id)
        if not attachment.exists():
            return None
        return attachment_to_resource_content(attachment, uri)

    # Check if field uses attachment storage
    if getattr(field, 'attachment', False):
        # Fetch from ir.attachment - handles URL type + binary/text
        # Use user's env (not sudo) to respect access rules on the attachment
        attachment = env['ir.attachment'].search([
            ('res_model', '=', model_name),
            ('res_field', '=', field_name),
            ('res_id', '=', record_id)
        ], limit=1)

        if not attachment:
            return None

        return attachment_to_resource_content(attachment, uri)

    # Read directly from field
    record = Model.browse(record_id)
    if not record.exists():
        return None

    # Use shared binary handling (handles empty, text detection, etc.)
    data = getattr(record, field_name, None)
    return binary_to_resource_content(data, uri)

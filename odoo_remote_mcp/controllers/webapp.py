# -*- coding: utf-8 -*-
"""
WebApp Controller

Serves React web applications with multi-page routing, data fetching,
API endpoints, and user storage.

Access is controlled via Odoo's native security:
- shared_user_ids: Share with specific users
- shared_group_ids: Share with groups (portal, public, or custom groups)

For public website access (no login), add base.group_public to shared_group_ids.
For portal user access, add base.group_portal to shared_group_ids.
"""

import base64
import io
import json
import logging
import re
import traceback

from markupsafe import escape as html_escape
from PIL import Image as PILImage

from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError
from odoo.tools.image import image_process

_logger = logging.getLogger(__name__)


class WebAppController(http.Controller):
    """Controller for React web applications."""

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _is_public_user(self):
        """Check if current user is the anonymous public user."""
        return request.env.user._is_public()

    @staticmethod
    def _json_script(data):
        """Serialize data for safe embedding inside <script> tags.

        Escapes '</' to '<\\/' to prevent the HTML parser from closing the
        script block prematurely when data contains '</script>' or similar.
        The '\\/' escape is valid JSON (RFC 8259) and decodes back to '/'
        in JavaScript, so the actual string values are preserved.
        """
        return json.dumps(data, default=str).replace('</', '<\\/')

    def _json_response(self, data, status=200):
        """Create a JSON response."""
        return request.make_response(
            json.dumps(data, default=str),
            status=status,
            headers={'Content-Type': 'application/json'}
        )

    def _json_error_response(self, exception, status=500):
        """Create a JSON error response with full traceback info."""
        error_data = {
            'error': str(exception),
            'error_type': type(exception).__name__,
            'traceback': traceback.format_exc(),
        }
        return request.make_response(
            json.dumps(error_data, default=str),
            status=status,
            headers={'Content-Type': 'application/json'}
        )

    # =========================================================================
    # App Rendering Routes
    # =========================================================================

    def _get_webapp(self, identifier):
        """
        Get webapp by ID or slug.

        :param identifier: Either an integer ID or a string slug
        :return: mcp.webapp record or empty recordset
        """
        WebApp = request.env['mcp.webapp']
        if isinstance(identifier, int):
            return WebApp.search([('id', '=', identifier)], limit=1)
        return WebApp.search([('slug', '=', identifier)], limit=1)

    @http.route([
        '/mcp/webapp/<int:app_id>',
        '/mcp/webapp/<string:slug>',
    ], type='http', auth='public', website=False)
    def view_webapp(self, app_id=None, slug=None, **kwargs):
        """
        Render the React web application.

        Supports both authenticated and anonymous access.
        Anonymous access requires base.group_public in shared_group_ids.
        Record rules enforce access control - no sudo() needed.

        Can be accessed by:
        - ID: /mcp/webapp/42
        - Slug under prefix: /mcp/webapp/my-app
        """
        identifier = app_id if app_id is not None else slug
        try:
            webapp = self._get_webapp(identifier)

            # If record doesn't exist or user lacks access, webapp will be empty (filtered by record rules)
            if not webapp.exists():
                if self._is_public_user():
                    # Redirect to login — use the URL the user actually visited
                    redirect_path = request.httprequest.path
                    return request.redirect(f'/web/login?redirect={redirect_path}')
                raise AccessError("You don't have access to this webapp.")

            # Increment view count
            webapp.sudo().increment_view_count()

            # Render with appropriate user context
            is_anonymous = self._is_public_user()
            return self._render_app_page(webapp, is_anonymous)

        except AccessError as e:
            return request.make_response(
                f"Access Denied: {e}",
                status=403,
                headers={'Content-Type': 'text/plain'}
            )
        except Exception as e:
            _logger.exception("Error rendering webapp %s", identifier)
            return self._render_error_page("Error", str(e))

    @http.route([
        '/mcp/webapp/<int:app_id>/<path:route>',
        '/mcp/webapp/<string:slug>/<path:route>',
    ], type='http', auth='public', website=False)
    def view_webapp_route(self, app_id=None, slug=None, route=None, **kwargs):
        """Catch-all route for SPA client-side routing."""
        return self.view_webapp(app_id=app_id, slug=slug, **kwargs)

    # =========================================================================
    # Page Data Routes
    # =========================================================================

    @http.route([
        '/mcp/webapp/<int:app_id>/page/<int:page_id>/data',
        '/mcp/webapp/<string:slug>/page/<int:page_id>/data',
    ], type='http', auth='public', website=False, methods=['GET'])
    def get_page_data(self, page_id, app_id=None, slug=None, **kwargs):
        """
        Fetch data for a specific page.

        Code executes with visitor's permissions (request.env) for security.
        """
        identifier = app_id if app_id is not None else slug
        try:
            webapp = self._get_webapp(identifier)
            if not webapp.exists():
                return self._json_response({'error': 'Access denied'}, status=403)

            page = request.env['mcp.webapp.page'].browse(page_id)
            if not page.exists() or page.webapp_id.id != webapp.id:
                return self._json_response({'error': 'Page not found'}, status=404)

            # Extract route params from query string
            route_params = {k: v for k, v in kwargs.items() if k not in ['debug']}

            # Execute data_code with visitor's permissions (request.env)
            data = page.fetch_page_data(route_params, env=request.env)
            return self._json_response(data)

        except Exception as e:
            _logger.exception("Error fetching page data for webapp %s, page %s", identifier, page_id)
            return self._json_error_response(e)

    # =========================================================================
    # User Storage Routes
    # =========================================================================

    def _get_storage(self, app_id, is_anonymous):
        """
        Get storage record for current user or session.

        Note: Uses sudo() because storage record rules don't have access to session context.
        Access control is handled by checking webapp access before calling this method.

        :param app_id: Webapp ID
        :param is_anonymous: Whether current user is anonymous
        :return: Storage record
        """
        Storage = request.env['mcp.webapp.user.storage'].sudo()
        if is_anonymous:
            return Storage.get_storage(app_id, session_id=request.session.sid)
        else:
            return Storage.get_storage(app_id, user_id=request.env.user.id)

    @http.route([
        '/mcp/webapp/<int:app_id>/storage',
        '/mcp/webapp/<string:slug>/storage',
    ], type='http', auth='public', website=False,
                methods=['GET', 'DELETE'], csrf=False)
    def storage_all(self, app_id=None, slug=None, **kwargs):
        """
        Get all storage data or clear storage.

        Works for both logged-in users (user-based) and anonymous users (session-based).
        """
        identifier = app_id if app_id is not None else slug
        try:
            webapp = self._get_webapp(identifier)
            if not webapp.exists():
                return self._json_response({'error': 'Access denied'}, status=403)

            is_anonymous = self._is_public_user()
            storage = self._get_storage(webapp.id, is_anonymous)

            if request.httprequest.method == 'DELETE':
                storage.clear_storage()
                return self._json_response({'success': True, 'message': 'Storage cleared'})

            return self._json_response(storage.get_all())

        except Exception as e:
            _logger.exception("Error accessing storage for webapp %s", identifier)
            return self._json_error_response(e)

    @http.route([
        '/mcp/webapp/<int:app_id>/storage/<string:key>',
        '/mcp/webapp/<string:slug>/storage/<string:key>',
    ], type='http', auth='public', website=False,
                methods=['GET', 'PUT', 'DELETE'], csrf=False)
    def storage_key(self, key, app_id=None, slug=None, **kwargs):
        """
        Get, set, or delete a specific storage key.

        Works for both logged-in users (user-based) and anonymous users (session-based).
        """
        identifier = app_id if app_id is not None else slug
        try:
            webapp = self._get_webapp(identifier)
            if not webapp.exists():
                return self._json_response({'error': 'Access denied'}, status=403)

            method = request.httprequest.method
            is_anonymous = self._is_public_user()
            storage = self._get_storage(webapp.id, is_anonymous)

            if method == 'GET':
                value = storage.get_value(key)
                return self._json_response({'key': key, 'value': value})

            elif method == 'PUT':
                try:
                    body = json.loads(request.httprequest.data.decode('utf-8'))
                    value = body.get('value')
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return self._json_response({'error': 'Invalid JSON body'}, status=400)

                storage.set_value(key, value)
                return self._json_response({'success': True, 'key': key, 'value': value})

            elif method == 'DELETE':
                deleted = storage.delete_value(key)
                return self._json_response({'success': True, 'key': key, 'deleted': deleted})

        except Exception as e:
            _logger.exception("Error accessing storage key %s for webapp %s", key, identifier)
            return self._json_error_response(e)

    # =========================================================================
    # Asset Routes
    # =========================================================================

    @http.route([
        '/mcp/webapp/<int:app_id>/asset/<string:filename>',
        '/mcp/webapp/<string:slug>/asset/<string:filename>',
    ], type='http', auth='public', website=False, methods=['GET'])
    def serve_asset(self, filename, app_id=None, slug=None, **kwargs):
        """Serve a binary asset (image, audio, etc.) linked to a webapp."""
        identifier = app_id if app_id is not None else slug
        try:
            webapp = self._get_webapp(identifier)
            if not webapp.exists():
                if self._is_public_user():
                    return request.make_response('Not Found', status=404)
                raise AccessError("You don't have access to this webapp.")

            attachment = webapp.sudo().asset_ids.filtered(
                lambda a: a.name == filename
            )[:1]
            if not attachment:
                return request.make_response('Asset not found', status=404)

            return request.make_response(
                attachment.raw,
                headers={
                    'Content-Type': attachment.mimetype or 'application/octet-stream',
                    'Cache-Control': 'public, max-age=604800',
                },
            )

        except AccessError as e:
            return request.make_response(
                f"Access Denied: {e}", status=403,
                headers={'Content-Type': 'text/plain'},
            )
        except Exception as e:
            _logger.exception("Error serving asset %s for webapp %s", filename, identifier)
            return request.make_response('Internal Server Error', status=500)

    # =========================================================================
    # PWA Routes
    # =========================================================================

    @http.route([
        '/mcp/webapp/<int:app_id>/manifest.json',
        '/mcp/webapp/<string:slug>/manifest.json',
    ], type='http', auth='public', website=False, methods=['GET'])
    def pwa_manifest(self, app_id=None, slug=None, **kwargs):
        """Serve PWA manifest.json for installable web apps."""
        identifier = app_id if app_id is not None else slug
        try:
            webapp = self._get_webapp(identifier)
            if not webapp.exists():
                return request.make_response('Not Found', status=404)

            app_identifier = webapp.slug or str(webapp.id)
            # PWA start_url/scope use bare slug for clean URLs, icons use /mcp/webapp/ prefix
            pwa_base = f'/{webapp.slug}' if webapp.slug else f'/mcp/webapp/{webapp.id}'
            asset_base = f'/mcp/webapp/{app_identifier}'

            manifest = {
                'name': webapp.name,
                'short_name': (webapp.name or '')[:12],
                'description': webapp.description or '',
                'start_url': pwa_base,
                'scope': pwa_base,
                'display': webapp.pwa_display or 'standalone',
                'theme_color': webapp.pwa_theme_color or '#714B67',
                'background_color': webapp.pwa_background_color or '#ffffff',
                'icons': [
                    {
                        'src': f'{asset_base}/icon/180',
                        'sizes': '180x180',
                        'type': 'image/png',
                        'purpose': 'any',
                    },
                    {
                        'src': f'{asset_base}/icon/192',
                        'sizes': '192x192',
                        'type': 'image/png',
                        'purpose': 'any',
                    },
                    {
                        'src': f'{asset_base}/icon/512',
                        'sizes': '512x512',
                        'type': 'image/png',
                        'purpose': 'any',
                    },
                ],
            }

            return request.make_response(
                json.dumps(manifest),
                headers={
                    'Content-Type': 'application/manifest+json',
                    'Cache-Control': 'public, max-age=86400',
                },
            )
        except Exception as e:
            _logger.exception("Error serving manifest for webapp %s", identifier)
            return request.make_response('Internal Server Error', status=500)

    @http.route([
        '/mcp/webapp/<int:app_id>/icon/<int:size>',
        '/mcp/webapp/<string:slug>/icon/<int:size>',
    ], type='http', auth='public', website=False, methods=['GET'])
    def pwa_icon(self, size, app_id=None, slug=None, **kwargs):
        """Serve resized webapp icon for PWA."""
        identifier = app_id if app_id is not None else slug
        try:
            webapp = self._get_webapp(identifier)
            if not webapp.exists():
                return request.make_response('Not Found', status=404)

            size = min(size, 1024)  # Cap size

            if webapp.thumbnail:
                # Resize thumbnail to requested square size
                png_data = image_process(
                    base64.b64decode(webapp.thumbnail),
                    size=(size, size),
                    crop='center',
                    output_format='PNG',
                )
                # Ensure exact dimensions (image_process won't upscale)
                img = PILImage.open(io.BytesIO(png_data))
                if img.size != (size, size):
                    img = img.resize((size, size), PILImage.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    png_data = buf.getvalue()
            else:
                # Generate a solid-color fallback square
                img = PILImage.new('RGB', (size, size), webapp.pwa_theme_color or '#714B67')
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                png_data = buf.getvalue()

            return request.make_response(
                png_data,
                headers={
                    'Content-Type': 'image/png',
                    'Cache-Control': 'public, max-age=604800',
                },
            )
        except Exception as e:
            _logger.exception("Error serving icon for webapp %s", identifier)
            return request.make_response('Internal Server Error', status=500)

    @http.route([
        '/mcp/webapp/<int:app_id>/sw.js',
        '/mcp/webapp/<string:slug>/sw.js',
    ], type='http', auth='public', website=False, methods=['GET'])
    def pwa_service_worker(self, app_id=None, slug=None, **kwargs):
        """Serve barebones PWA service worker (required for installability, no caching)."""
        identifier = app_id if app_id is not None else slug
        try:
            webapp = self._get_webapp(identifier)
            if not webapp.exists():
                return request.make_response('Not Found', status=404)

            pwa_base = f'/{webapp.slug}' if webapp.slug else f'/mcp/webapp/{webapp.id}'

            sw_code = "// Barebones service worker for PWA installability\nself.addEventListener('fetch', () => {});\n"

            return request.make_response(
                sw_code,
                headers={
                    'Content-Type': 'application/javascript',
                    'Cache-Control': 'no-cache',
                    'Service-Worker-Allowed': pwa_base,
                },
            )
        except Exception as e:
            _logger.exception("Error serving service worker for webapp %s", identifier)
            return request.make_response('Internal Server Error', status=500)

    # =========================================================================
    # API Endpoint Routes
    # =========================================================================

    @http.route([
        '/mcp/webapp/<int:app_id>/api/<path:endpoint_path>',
        '/mcp/webapp/<string:slug>/api/<path:endpoint_path>',
    ], type='http', auth='public', website=False,
                methods=['GET', 'POST', 'PUT', 'DELETE'], csrf=False)
    def api_endpoint(self, endpoint_path, app_id=None, slug=None, **kwargs):
        """
        Handle API endpoint requests.

        Endpoint code executes with visitor's permissions (request.env).
        """
        identifier = app_id if app_id is not None else slug
        try:
            webapp = self._get_webapp(identifier)
            if not webapp.exists():
                return self._json_response({'error': 'Access denied'}, status=403)

            return self._execute_endpoint(webapp, endpoint_path, kwargs)

        except Exception as e:
            _logger.exception("Error executing API endpoint for webapp %s: %s", identifier, endpoint_path)
            return self._json_error_response(e)

    # =========================================================================
    # Endpoint Execution
    # =========================================================================

    def _match_endpoint(self, webapp, request_path, method):
        """Find matching endpoint and extract route params."""
        request_path = request_path.strip('/')
        request_parts = request_path.split('/') if request_path else []

        for endpoint in webapp.endpoint_ids:
            if endpoint.method != method:
                continue

            endpoint_path = endpoint.endpoint_path.strip('/')
            endpoint_parts = endpoint_path.split('/') if endpoint_path else []

            if len(request_parts) != len(endpoint_parts):
                continue

            route_params = {}
            match = True

            for req_part, ep_part in zip(request_parts, endpoint_parts):
                if ep_part.startswith(':'):
                    param_name = ep_part[1:]
                    route_params[param_name] = req_part
                elif req_part != ep_part:
                    match = False
                    break

            if match:
                return endpoint, route_params

        return None, None

    def _execute_endpoint(self, webapp, endpoint_path, kwargs):
        """
        Execute the matched endpoint handler.

        Handler code executes with visitor's permissions (request.env).
        """
        method = request.httprequest.method
        endpoint, route_params = self._match_endpoint(webapp, endpoint_path, method)

        if not endpoint:
            return self._json_response(
                {'error': f'Endpoint not found: {method} {endpoint_path}'},
                status=404
            )

        # Get request body for POST/PUT
        body = {}
        if method in ('POST', 'PUT'):
            try:
                content_type = request.httprequest.content_type or ''
                if 'application/json' in content_type:
                    body = json.loads(request.httprequest.data.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        query_params = {k: v for k, v in kwargs.items() if k not in ['debug']}

        # Execute with visitor's permissions (request.env)
        result = endpoint.execute_handler(
            query_params, body, route_params,
            env=request.env, http_request=request,
        )

        return self._json_response(result)

    # =========================================================================
    # Page Rendering
    # =========================================================================

    def _build_cdn_scripts(self, cdn_dependencies):
        """Build script tags for additional CDN dependencies."""
        if not cdn_dependencies:
            return ''
        scripts = []
        for url in cdn_dependencies.strip().split('\n'):
            url = url.strip()
            if url and url.startswith('http'):
                scripts.append(f'    <script src="{url}"></script>')
        return '\n'.join(scripts)

    def _build_import_map(self, webapp):
        """Build the import map for ESM dependencies."""
        import_map = {
            "imports": {
                "react": "https://esm.sh/react@19",
                "react/": "https://esm.sh/react@19/",
                "react-dom/client": "https://esm.sh/react-dom@19/client",
                "react-router-dom": "https://esm.sh/react-router-dom@6",
                "react-error-boundary": "https://esm.sh/react-error-boundary@5?external=react"
            }
        }

        if webapp.cdn_dependencies:
            try:
                custom_imports = json.loads(webapp.cdn_dependencies)
                if isinstance(custom_imports, dict):
                    import_map["imports"].update(custom_imports)
            except json.JSONDecodeError:
                pass

        return json.dumps(import_map, indent=6)

    def _generate_page_components(self, webapp):
        """Generate all page component code."""
        components = []
        for page in webapp.page_ids:
            component_name = page.get_component_name()

            # Inject component files before the main component (same scope)
            for f in page.component_file_ids.sorted('sequence'):
                components.append(f'        // --- {page.name}: {f.name} ---\n        {f.code}')

            # Main component code
            code = page.component_code or 'function() { return <div>Empty page</div>; }'
            stripped = code.strip()
            if not re.match(r'^\s*(function|const|let|var)\s+', stripped):
                # Wrap raw JSX with destructured props so globalState, data, etc. are available
                code = f'function {component_name}({{data, routeParams, globalState, setGlobalState, initialData, api, storage, user, asset}}) {{ return ({code}); }}'
            elif stripped.startswith('function'):
                # Rename any function (named or anonymous) to the expected component name
                code = re.sub(r'^(\s*)function\s*\w*\s*\(', f'\\1function {component_name}(', code, count=1)
            elif re.match(r'^\s*(const|let|var)\s+', stripped):
                # Rename const/let/var declarations: const Foo = ... → const GamePage = ...
                code = re.sub(r'^(\s*(?:const|let|var)\s+)\w+', f'\\1{component_name}', code, count=1)
            components.append(f'        // === {page.name} ===\n        {code}')
        return '\n\n'.join(components)

    def _generate_routes(self, webapp):
        """Generate React Router routes."""
        routes = []
        for page in webapp.page_ids:
            component_name = page.get_component_name()
            path = page.route_path or '/'
            page_id = page.id
            title = json.dumps(page.page_title or page.name)
            has_data = 'true' if page.data_code else 'false'
            routes.append(
                f'<Route path="{path}" element={{<PageWrapper Component={{{component_name}}} pageId={{{page_id}}} pageTitle={{{title}}} hasData={{{has_data}}} />}} />'
            )
        return '\n                            '.join(routes)

    def _build_user_context(self, is_anonymous=False):
        """
        Build user context object for frontend.

        :param is_anonymous: Whether current user is anonymous
        :return: Dict with user information
        """
        user = request.env.user

        if is_anonymous:
            return {
                'id': False,
                'name': 'Guest',
                'email': False,
                'login': False,
                'is_public': True,
                'is_portal': False,
                'is_internal': False,
                'is_system': False,
            }

        return {
            'id': user.id,
            'name': user.name,
            'email': user.email or False,
            'login': user.login,
            'is_public': user._is_public(),
            'is_portal': user.has_group('base.group_portal'),
            'is_internal': user.has_group('base.group_user'),
            'is_system': user.has_group('base.group_system'),
            'company_id': user.company_id.id if user.company_id else False,
            'company_name': user.company_id.name if user.company_id else False,
        }

    def _render_app_page(self, webapp, is_anonymous=False):
        """
        Render the React application HTML page.

        Initial data code executes with visitor's permissions (request.env).
        """
        try:
            # Fetch initial data with visitor's permissions
            try:
                initial_data = webapp.fetch_initial_data(env=request.env)
            except Exception as e:
                _logger.exception("Error fetching initial data for webapp %s", webapp.id)
                return self._render_error_page(webapp.name, f"Error fetching initial data: {e}")

            # Build user context
            user_context = self._build_user_context(is_anonymous)

            # Build components
            import_map = self._build_import_map(webapp)
            page_components = self._generate_page_components(webapp)
            routes = self._generate_routes(webapp)
            shared_components = webapp.shared_components or ''
            shared_styles = webapp.shared_styles or ''
            global_state = webapp.global_state_code or '{}'
            custom_imports = webapp.custom_imports or ''

            # Use slug for all URL references (portable across environments), fallback to ID
            app_identifier = webapp.slug or str(webapp.id)
            api_base = f'/mcp/webapp/{app_identifier}'

            # SEO / Open Graph meta tags
            base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
            # Canonical URL uses bare slug when available
            canonical_url = f'{base_url}/{webapp.slug}' if webapp.slug else f'{base_url}/mcp/webapp/{webapp.id}'
            seo_description = html_escape(webapp.description or '')
            seo_title = html_escape(webapp.name)
            site_name = html_escape(
                request.env['ir.config_parameter'].sudo().get_param('web.base.url', '').split('://')[-1]
            )

            seo_head = f'<link rel="canonical" href="{canonical_url}">'
            seo_head += f'\n    <meta property="og:type" content="website">'
            seo_head += f'\n    <meta property="og:title" content="{seo_title}">'
            seo_head += f'\n    <meta property="og:url" content="{canonical_url}">'
            seo_head += f'\n    <meta property="og:site_name" content="{site_name}">'
            seo_head += f'\n    <meta name="twitter:card" content="summary">'
            seo_head += f'\n    <meta name="twitter:title" content="{seo_title}">'
            if webapp.description:
                seo_head += f'\n    <meta name="description" content="{seo_description}">'
                seo_head += f'\n    <meta property="og:description" content="{seo_description}">'
                seo_head += f'\n    <meta name="twitter:description" content="{seo_description}">'
            if webapp.thumbnail:
                og_image = f'{base_url}/mcp/webapp/{app_identifier}/icon/512'
                seo_head += f'\n    <meta property="og:image" content="{og_image}">'
                seo_head += f'\n    <meta property="og:image:width" content="512">'
                seo_head += f'\n    <meta property="og:image:height" content="512">'
                seo_head += f'\n    <meta name="twitter:image" content="{og_image}">'

            # PWA meta tags
            pwa_head = ''
            pwa_body = ''
            if webapp.pwa_enabled:
                pwa_theme = html_escape(webapp.pwa_theme_color or '#714B67')
                pwa_name = html_escape(webapp.name)
                pwa_base = f'/{webapp.slug}' if webapp.slug else f'/mcp/webapp/{webapp.id}'
                pwa_head = f'''
    <link rel="manifest" href="/mcp/webapp/{app_identifier}/manifest.json" crossorigin="use-credentials">
    <meta name="theme-color" content="{pwa_theme}">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="default">
    <meta name="apple-mobile-web-app-title" content="{pwa_name}">
    <link rel="apple-touch-icon" sizes="180x180" href="/mcp/webapp/{app_identifier}/icon/180">'''
                pwa_body = f'''
    <script>
    if ('serviceWorker' in navigator) {{
        navigator.serviceWorker.register('/mcp/webapp/{app_identifier}/sw.js', {{
            scope: '{pwa_base}'
        }});
    }}
    </script>'''

            tailwind_script = ''
            if webapp.tailwind_enabled:
                tailwind_script = '<script src="https://cdn.tailwindcss.com"></script>'

            router_component = 'HashRouter' if webapp.router_mode == 'hash' else 'MemoryRouter'

            cdn_scripts = ''
            if webapp.cdn_dependencies:
                try:
                    json.loads(webapp.cdn_dependencies)
                except json.JSONDecodeError:
                    cdn_scripts = self._build_cdn_scripts(webapp.cdn_dependencies)

            # No longer need to pass anonymous flag - server handles both cases

            html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html_escape(webapp.name)}</title>
    {seo_head}
    {pwa_head}

    <!-- Import Map for ESM dependencies -->
    <script type="importmap">
    {import_map}
    </script>

    <!-- Babel for JSX transformation -->
    <script src="https://unpkg.com/@babel/standalone@7.28.6/babel.min.js"></script>

    <!-- Tailwind CSS -->
    {tailwind_script}

    <!-- Additional CDN dependencies -->
{cdn_scripts}

    <style>
        /* User-customizable styles */
        {shared_styles}

        /* Framework styles */
        .mcp-loading {{
            display: flex;
            align-items: center;
            justify-content: center;
            height: 16rem;
            color: #6b7280;
        }}
        .mcp-error {{
            color: #dc2626;
            padding: 1rem;
        }}
    </style>
</head>
<body>
    <div id="root">
        <div class="mcp-loading">Loading application...</div>
    </div>

    <script type="text/babel" data-type="module" data-presets="react">
        import React, {{ useState, useEffect, useCallback, useMemo, useRef, useReducer, useId, useContext, createContext }} from 'react';
        import {{ createRoot }} from 'react-dom/client';
        import {{ {router_component}, Routes, Route, Link, useParams, useNavigate, useLocation, useSearchParams, NavLink, Navigate, Outlet }} from 'react-router-dom';
        import {{ ErrorBoundary }} from 'react-error-boundary';
        {custom_imports}

        // Initial data from server
        const __INITIAL_DATA__ = {self._json_script(initial_data)};
        const __API_BASE__ = '{api_base}';
        const __USER_CONTEXT__ = {self._json_script(user_context)};

        // Global state context
        const AppContext = createContext();
        const useApp = () => useContext(AppContext);

        // API helper with error handling
        const handleApiResponse = async (response) => {{
            const contentType = response.headers.get('content-type') || '';
            if (!contentType.includes('application/json')) {{
                const text = await response.text();
                const status = response.status;
                let msg = `Server returned ${{status}} (${{response.statusText}})`;
                if (status === 502 || status === 504) msg = 'Server timeout - the request took too long. Try a simpler prompt or retry.';
                else if (status === 403) msg = 'Access denied - your session may have expired. Try refreshing the page.';
                else if (text.includes('<!DOCTYPE') || text.includes('<html')) msg += ' - received HTML instead of JSON (possible proxy timeout or server error).';
                throw new Error(msg);
            }}
            const json = await response.json();
            if (!response.ok || json.error) {{
                const error = new Error(json.error || response.statusText);
                error.details = json;
                throw error;
            }}
            return json;
        }};

        const createApi = (baseUrl) => ({{
            get: (path, params) => {{
                const query = params ? '?' + new URLSearchParams(params).toString() : '';
                return fetch(`${{baseUrl}}/api/${{path}}${{query}}`).then(handleApiResponse);
            }},
            post: (path, body) => fetch(`${{baseUrl}}/api/${{path}}`, {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify(body)
            }}).then(handleApiResponse),
            put: (path, body) => fetch(`${{baseUrl}}/api/${{path}}`, {{
                method: 'PUT',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify(body)
            }}).then(handleApiResponse),
            delete: (path) => fetch(`${{baseUrl}}/api/${{path}}`, {{method: 'DELETE'}}).then(handleApiResponse),
        }});

        // Storage helper - server storage with localStorage cache for offline fallback
        const STORAGE_KEY_PREFIX = 'mcp_webapp_{webapp.id}_';

        const createStorage = (baseUrl) => {{
            // localStorage helpers for caching and offline fallback
            const localGet = (key, defaultValue) => {{
                try {{
                    const item = localStorage.getItem(STORAGE_KEY_PREFIX + key);
                    return item !== null ? JSON.parse(item) : defaultValue;
                }} catch {{ return defaultValue; }}
            }};
            const localSet = (key, value) => {{
                try {{ localStorage.setItem(STORAGE_KEY_PREFIX + key, JSON.stringify(value)); return true; }}
                catch {{ return false; }}
            }};
            const localDelete = (key) => {{
                try {{ localStorage.removeItem(STORAGE_KEY_PREFIX + key); return true; }}
                catch {{ return false; }}
            }};
            const localGetAll = () => {{
                const data = {{}};
                try {{
                    for (let i = 0; i < localStorage.length; i++) {{
                        const k = localStorage.key(i);
                        if (k && k.startsWith(STORAGE_KEY_PREFIX)) {{
                            data[k.slice(STORAGE_KEY_PREFIX.length)] = JSON.parse(localStorage.getItem(k));
                        }}
                    }}
                }} catch {{}}
                return data;
            }};
            const localClear = () => {{
                try {{
                    const keysToRemove = [];
                    for (let i = 0; i < localStorage.length; i++) {{
                        const k = localStorage.key(i);
                        if (k && k.startsWith(STORAGE_KEY_PREFIX)) keysToRemove.push(k);
                    }}
                    keysToRemove.forEach(k => localStorage.removeItem(k));
                    return true;
                }} catch {{ return false; }}
            }};

            // Unified storage: server storage with localStorage cache
            return {{
                get: async (key, defaultValue = null) => {{
                    try {{
                        const response = await fetch(`${{baseUrl}}/storage/${{encodeURIComponent(key)}}`);
                        const json = await response.json();
                        if (!response.ok || json.error) return localGet(key, defaultValue);
                        const value = json.value !== null ? json.value : defaultValue;
                        localSet(key, value); // Cache locally
                        return value;
                    }} catch {{
                        return localGet(key, defaultValue); // Fallback to cache
                    }}
                }},
                set: async (key, value) => {{
                    localSet(key, value); // Cache immediately for optimistic UI
                    try {{
                        const response = await fetch(`${{baseUrl}}/storage/${{encodeURIComponent(key)}}`, {{
                            method: 'PUT',
                            headers: {{'Content-Type': 'application/json'}},
                            body: JSON.stringify({{ value }})
                        }});
                        return response.ok;
                    }} catch {{ return false; }}
                }},
                delete: async (key) => {{
                    localDelete(key);
                    try {{
                        const response = await fetch(`${{baseUrl}}/storage/${{encodeURIComponent(key)}}`, {{
                            method: 'DELETE'
                        }});
                        return response.ok;
                    }} catch {{ return false; }}
                }},
                getAll: async () => {{
                    try {{
                        const response = await fetch(`${{baseUrl}}/storage`);
                        const json = await response.json();
                        if (!response.ok || json.error) return localGetAll();
                        return json;
                    }} catch {{ return localGetAll(); }}
                }},
                clear: async () => {{
                    localClear();
                    try {{
                        const response = await fetch(`${{baseUrl}}/storage`, {{ method: 'DELETE' }});
                        return response.ok;
                    }} catch {{ return false; }}
                }},
            }};
        }};

        // Asset URL helper
        const asset = (filename) => `${{__API_BASE__}}/asset/${{encodeURIComponent(filename)}}`;

        // Shared components
        {shared_components}

        // Page components
        {page_components}

        // Error display component
        function ErrorDisplay({{ error, onRetry }}) {{
            const [expanded, setExpanded] = useState(true);
            const errorStyle = {{
                padding: '1rem',
                background: '#fef2f2',
                border: '1px solid #fecaca',
                borderRadius: '0.5rem',
                margin: '1rem',
                fontFamily: 'system-ui, sans-serif',
            }};
            const headerStyle = {{
                color: '#dc2626',
                fontSize: '1.1rem',
                fontWeight: '600',
                marginBottom: '0.5rem',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
            }};
            const messageStyle = {{
                color: '#991b1b',
                marginBottom: '0.75rem',
                padding: '0.5rem',
                background: '#fee2e2',
                borderRadius: '0.25rem',
                wordBreak: 'break-word',
            }};
            const tracebackStyle = {{
                fontFamily: 'ui-monospace, monospace',
                fontSize: '0.75rem',
                background: '#1f2937',
                color: '#f9fafb',
                padding: '1rem',
                borderRadius: '0.375rem',
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                maxHeight: expanded ? '400px' : '0',
                transition: 'max-height 0.2s',
            }};
            const toggleStyle = {{
                cursor: 'pointer',
                color: '#6b7280',
                fontSize: '0.875rem',
                marginTop: '0.5rem',
                userSelect: 'none',
            }};
            const buttonStyle = {{
                marginTop: '1rem',
                padding: '0.5rem 1rem',
                background: '#714B67',
                color: 'white',
                border: 'none',
                borderRadius: '0.375rem',
                cursor: 'pointer',
                fontSize: '0.875rem',
            }};

            return (
                <div style={{errorStyle}}>
                    <div style={{headerStyle}}>
                        <span>\u26a0\ufe0f</span>
                        <span>{{error.error_type || 'Error'}}</span>
                    </div>
                    <div style={{messageStyle}}>{{error.error || error.message || 'An error occurred'}}</div>
                    {{error.traceback && (
                        <>
                            <div style={{toggleStyle}} onClick={{() => setExpanded(!expanded)}}>
                                {{expanded ? '\u25bc' : '\u25b6'}} Traceback (click to {{expanded ? 'collapse' : 'expand'}})
                            </div>
                            {{expanded && <pre style={{tracebackStyle}}>{{error.traceback}}</pre>}}
                        </>
                    )}}
                    <button style={{buttonStyle}} onClick={{onRetry || (() => window.location.reload())}}>
                        Reload Page
                    </button>
                </div>
            );
        }}

        function ErrorFallback({{ error, resetErrorBoundary }}) {{
            const errorObj = {{
                error: error.message || String(error),
                error_type: error.name || 'JavaScript Error',
                traceback: error.stack || '',
            }};
            return <ErrorDisplay error={{errorObj}} onRetry={{resetErrorBoundary}} />;
        }}

        function PageWrapper({{ Component, pageId, pageTitle, hasData }}) {{
            const [data, setData] = useState(hasData ? null : {{}});
            const [loading, setLoading] = useState(!!hasData);
            const [error, setError] = useState(null);
            const params = useParams();
            const context = useApp();

            useEffect(() => {{
                if (pageTitle) document.title = pageTitle;
                if (!hasData) return;

                setLoading(true);
                setError(null);

                const queryString = new URLSearchParams(params).toString();
                fetch(`${{__API_BASE__}}/page/${{pageId}}/data${{queryString ? '?' + queryString : ''}}`)
                    .then(async r => {{
                        const json = await r.json();
                        if (!r.ok || json.error) throw json;
                        return json;
                    }})
                    .then(data => {{ setData(data); setLoading(false); }})
                    .catch(e => {{
                        if (e && typeof e === 'object' && (e.error || e.traceback)) {{
                            setError(e);
                        }} else {{
                            setError({{ error: e.toString ? e.toString() : String(e) }});
                        }}
                        setLoading(false);
                    }});
            }}, [pageId, hasData, JSON.stringify(params)]);

            if (loading) return <div className="mcp-loading">Loading...</div>;
            if (error) return <ErrorDisplay error={{error}} onRetry={{() => window.location.reload()}} />;

            return (
                <ErrorBoundary key={{pageId}} FallbackComponent={{ErrorFallback}}
                    onError={{(error, info) => console.error('Component error:', error.message, info.componentStack)}}>
                    <ComponentRenderer Component={{Component}} data={{data}} params={{params}} context={{context}} />
                </ErrorBoundary>
            );
        }}

        function ComponentRenderer({{ Component, data, params, context }}) {{
            return (
                <Component
                    data={{data}}
                    routeParams={{params}}
                    globalState={{context.globalState}}
                    setGlobalState={{context.setGlobalState}}
                    initialData={{context.initialData}}
                    api={{context.api}}
                    storage={{context.storage}}
                    user={{context.user}}
                    asset={{context.asset}}
                />
            );
        }}

        let __INITIAL_GLOBAL_STATE__ = {{}};
        try {{
            __INITIAL_GLOBAL_STATE__ = {global_state};
        }} catch(e) {{
            console.error('Error in global_state_code:', e);
        }}

        function App() {{
            const [globalState, setGlobalState] = useState(__INITIAL_GLOBAL_STATE__);
            const api = createApi(__API_BASE__);
            const storage = createStorage(__API_BASE__);
            const user = __USER_CONTEXT__;

            return (
                <AppContext.Provider value={{{{globalState, setGlobalState, initialData: __INITIAL_DATA__, api, storage, user, asset}}}}>
                    <{router_component}>
                        <Routes>
                            {routes}
                        </Routes>
                    </{router_component}>
                </AppContext.Provider>
            );
        }}

        const root = createRoot(document.getElementById('root'));
        root.render(
            <ErrorBoundary FallbackComponent={{ErrorFallback}}>
                <App />
            </ErrorBoundary>
        );
    </script>
    {pwa_body}
</body>
</html>'''

            return request.make_response(html, headers={'Content-Type': 'text/html'})

        except Exception as e:
            _logger.exception("Error rendering webapp %s", webapp.id)
            return self._render_error_page(webapp.name, str(e))

    def _render_error_page(self, title, error_message):
        """Render an error page."""
        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Error - {html_escape(title)}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background: #f8f9fa;
        }}
        .error-box {{
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            max-width: 500px;
            text-align: center;
        }}
        .error-box h1 {{ color: #dc3545; margin-bottom: 20px; }}
        .error-box p {{ color: #666; line-height: 1.6; }}
        .error-box a {{ color: #714B67; text-decoration: none; }}
    </style>
</head>
<body>
    <div class="error-box">
        <h1>Error</h1>
        <p>{html_escape(error_message)}</p>
        <p><a href="javascript:history.back()">&#8592; Go Back</a></p>
    </div>
</body>
</html>'''
        return request.make_response(html, headers={'Content-Type': 'text/html'})

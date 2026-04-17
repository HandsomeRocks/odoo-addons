# -*- coding: utf-8 -*-
"""
EChart Standalone View Controller

Serves standalone HTML pages for viewing ECharts dashboards.
"""

import json
import logging
import re

from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class EChartController(http.Controller):
    """Controller for standalone EChart views."""

    @http.route('/mcp/echart/<int:echart_id>', type='http', auth='user', website=False)
    def view_echart(self, echart_id, **kwargs):
        """
        Render a standalone page for viewing an EChart dashboard.

        :param echart_id: ID of the mcp.echart record
        :return: HTML page with the chart
        """
        try:
            echart = request.env['mcp.echart'].browse(echart_id)

            if not echart.exists():
                return request.not_found()

            # Check access - user must be creator, or chart must be shared
            user = request.env.user
            has_access = (
                echart.user_id.id == user.id or
                echart.share_with_all_users or
                user.id in echart.shared_user_ids.ids or
                bool(set(user.groups_id.ids) & set(echart.shared_group_ids.ids))
            )
            if not has_access:
                raise AccessError("You don't have access to this chart.")

            # Fetch the chart data
            try:
                data = echart.fetch_chart_data()
            except Exception as e:
                _logger.exception("Error fetching chart data for echart %s", echart_id)
                return self._render_error_page(echart.name, f"Error fetching data: {e}")

            # Replace placeholders in chart options
            chart_options = echart.chart_options or {}
            try:
                options = self._replace_placeholders(chart_options, data)
            except Exception as e:
                _logger.exception("Error processing chart options for echart %s", echart_id)
                return self._render_error_page(echart.name, f"Error processing chart options: {e}")

            # Render the standalone page
            embed = kwargs.get('embed') == '1'
            return self._render_chart_page(echart, options, data, embed=embed)

        except AccessError as e:
            return request.make_response(
                f"Access Denied: {e}",
                status=403,
                headers={'Content-Type': 'text/plain'}
            )
        except Exception as e:
            _logger.exception("Error rendering echart %s", echart_id)
            return self._render_error_page("Error", str(e))

    @http.route('/mcp/echart/public/<int:echart_id>/<string:token>',
                type='http', auth='public', website=False)
    def view_echart_public(self, echart_id, token, **kwargs):
        """
        Render a standalone page for viewing an EChart dashboard via public link.

        :param echart_id: ID of the mcp.echart record
        :param token: Public access token
        :return: HTML page with the chart
        """
        try:
            # Validate token and get echart (runs as sudo internally)
            echart = request.env['mcp.echart'].validate_public_token(echart_id, token)

            if not echart:
                return self._render_error_page(
                    "Access Denied",
                    "Invalid or expired public link."
                )

            # Fetch the chart data (using sudo since public access)
            try:
                data = echart.fetch_chart_data()
            except Exception as e:
                _logger.exception("Error fetching chart data for echart %s (public)", echart_id)
                return self._render_error_page(echart.name, f"Error fetching data: {e}")

            # Replace placeholders in chart options
            chart_options = echart.chart_options or {}
            try:
                options = self._replace_placeholders(chart_options, data)
            except Exception as e:
                _logger.exception("Error processing chart options for echart %s (public)", echart_id)
                return self._render_error_page(echart.name, f"Error processing chart options: {e}")

            # Render the standalone page
            embed = kwargs.get('embed') == '1'
            return self._render_chart_page(echart, options, data, embed=embed)

        except Exception as e:
            _logger.exception("Error rendering public echart %s", echart_id)
            return self._render_error_page("Error", str(e))

    def _replace_placeholders(self, obj, data):
        """Replace $data and $data.xxx placeholders with actual values."""
        json_str = json.dumps(obj)

        def replacer(match):
            path = match.group(1)
            if not path:
                # "$data" — return the entire data object
                return json.dumps(data)
            value = data
            for key in path.split('.'):
                if isinstance(value, dict):
                    value = value.get(key)
                elif isinstance(value, (list, tuple)):
                    try:
                        value = value[int(key)]
                    except (ValueError, IndexError):
                        value = None
                        break
                else:
                    value = None
                    break
            return json.dumps(value)

        replaced = re.sub(r'"\$data(?:\.([^"]+))?"', replacer, json_str)
        return json.loads(replaced)

    def _build_extension_scripts(self, extension_urls):
        """Build script tags for extension URLs."""
        if not extension_urls:
            return ''
        scripts = []
        for url in extension_urls.strip().split('\n'):
            url = url.strip()
            if url and url.startswith('http'):
                scripts.append(f'    <script src="{url}"></script>')
        return '\n'.join(scripts)

    def _render_chart_page(self, echart, options, data, embed=False):
        """Render the standalone chart HTML page using ECharts native patterns.

        Supports two formats for options:
        - Single dict: One chart instance (backwards compatible)
        - List of dicts: Multiple chart instances stacked vertically

        :param embed: If True, hides the header bar for clean iframe embedding
        """
        title = echart.name
        header_display = ' style="display:none"' if embed else ''
        extension_scripts = self._build_extension_scripts(echart.extension_urls)
        pre_init_js = echart.pre_init_js or ''
        post_init_js = echart.post_init_js or ''
        renderer = echart.renderer or 'canvas'
        media_queries = echart.media_queries or []

        # Normalize options to list format
        if isinstance(options, dict):
            options_list = [options]
        elif isinstance(options, list):
            options_list = options
        else:
            options_list = [{}]

        # Generate chart panel divs
        chart_divs = '\n'.join(
            f'    <div class="chart-panel" id="chart-{i}"></div>'
            for i in range(len(options_list))
        )

        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>{title} - Odoo EChart</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@6/dist/echarts.min.js"></script>
{extension_scripts}
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        html, body {{
            width: 100%;
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: white;
        }}
        .header {{
            height: 50px;
            background: #714B67;
            color: white;
            display: flex;
            align-items: center;
            padding: 0 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            position: sticky;
            top: 0;
            z-index: 100;
        }}
        .header h1 {{
            font-size: 18px;
            font-weight: 500;
        }}
        .chart-panel {{
            width: 100%;
        }}
        @media (max-width: 600px) {{
            .header {{
                height: 40px;
                padding: 0 12px;
            }}
            .header h1 {{
                font-size: 15px;
            }}
        }}
    </style>
</head>
<body>
    <div class="header"{header_display}>
        <h1>{title}</h1>
    </div>
{chart_divs}
    <script>
        // Revive __fn__: string markers into actual JavaScript functions.
        // The data parameter is injected into each function's closure so
        // formatters can reference chart data directly (e.g., data.total).
        function reviveFunctions(obj, data) {{
            if (typeof obj === 'string' && obj.startsWith('__fn__:')) {{
                try {{
                    return new Function('data', 'return (' + obj.slice(7) + ')')(data);
                }} catch (e) {{
                    console.error('Error parsing function:', obj, e);
                    return obj;
                }}
            }}
            if (Array.isArray(obj)) {{
                return obj.map(function(item) {{ return reviveFunctions(item, data); }});
            }}
            if (obj && typeof obj === 'object') {{
                const result = {{}};
                for (const [k, v] of Object.entries(obj)) {{
                    result[k] = reviveFunctions(v, data);
                }}
                return result;
            }}
            return obj;
        }}

        // Calculate required height based on chart options
        function calculatePanelHeight(opts, numPanels) {{
            const isMobile = window.innerWidth < 600;
            const headerHeight = {'0' if embed else '(isMobile ? 40 : 50)'};
            const viewportHeight = window.innerHeight - headerHeight;

            // For multiple panels, distribute height but ensure minimum
            if (numPanels > 1) {{
                const minPanel = isMobile ? 300 : 400;
                const equalShare = Math.max(minPanel, viewportHeight / numPanels);

                // Check if this panel has multiple grids (needs more space)
                if (opts.grid && Array.isArray(opts.grid) && opts.grid.length > 1) {{
                    return Math.max(equalShare, isMobile ? 400 : 600);
                }}

                // Check for pie/gauge/radar - they work well in square-ish containers
                const hasPieType = opts.series && opts.series.some(s =>
                    ['pie', 'radar', 'gauge', 'sunburst', 'treemap', 'funnel'].includes(s.type)
                );
                if (hasPieType) {{
                    return Math.max(minPanel, Math.min(viewportHeight * 0.5, window.innerWidth * 0.6));
                }}

                return equalShare;
            }}

            // Single panel
            const minHeight = Math.max(viewportHeight, isMobile ? 400 : 600);

            // Check for multiple grids (complex dashboard)
            if (opts.grid && Array.isArray(opts.grid) && opts.grid.length > 1) {{
                let maxBottom = 0;
                opts.grid.forEach(g => {{
                    let bottom = 0;
                    if (g.bottom !== undefined) {{
                        if (typeof g.bottom === 'string' && g.bottom.endsWith('%')) {{
                            bottom = 100 - parseFloat(g.bottom);
                        }} else {{
                            bottom = 100;
                        }}
                    }} else if (g.top !== undefined && g.height !== undefined) {{
                        let top = typeof g.top === 'string' && g.top.endsWith('%')
                            ? parseFloat(g.top) : (g.top / viewportHeight * 100);
                        let height = typeof g.height === 'string' && g.height.endsWith('%')
                            ? parseFloat(g.height) : (g.height / viewportHeight * 100);
                        bottom = top + height;
                    }}
                    maxBottom = Math.max(maxBottom, bottom);
                }});

                if (maxBottom > 80) {{
                    return Math.max(minHeight, viewportHeight * 1.8);
                }}
            }}

            if (opts.grid && !Array.isArray(opts.grid) && opts.grid.height) {{
                if (typeof opts.grid.height === 'number') {{
                    return Math.max(minHeight, opts.grid.height + 100);
                }}
            }}

            return minHeight;
        }}

        // Initialize all chart panels
        function initCharts() {{
            try {{
                var data = {json.dumps(data)};
                const optionsList = {json.dumps(options_list)}.map(function(opt) {{ return reviveFunctions(opt, data); }});
                const mediaQueries = {json.dumps(media_queries)};
                const numPanels = optionsList.length;
                const charts = [];

                optionsList.forEach((panelOptions, index) => {{
                    const chartDom = document.getElementById('chart-' + index);
                    if (!chartDom) return;

                    // Calculate and set height for this panel
                    const panelHeight = calculatePanelHeight(panelOptions, numPanels);
                    chartDom.style.height = panelHeight + 'px';

                    // Ensure container has dimensions (required for echarts-gl)
                    if (chartDom.offsetWidth === 0 || chartDom.offsetHeight === 0) {{
                        requestAnimationFrame(initCharts);
                        return;
                    }}

                    // For first panel only: run pre-init JS
                    // Available vars: chartDom, panelOptions (as baseOptions/options), data, mediaQueries
                    if (index === 0) {{
                        const baseOptions = panelOptions;
                        const options = panelOptions;  // Alias for backwards compat
                        {pre_init_js}
                    }}

                    // Initialize chart with selected renderer
                    const chart = echarts.init(chartDom, null, {{ renderer: '{renderer}' }});
                    charts.push({{ chart, options: panelOptions, dom: chartDom }});

                    // Use compound option if media queries exist
                    if (mediaQueries && mediaQueries.length > 0) {{
                        chart.setOption({{
                            baseOption: panelOptions,
                            media: mediaQueries.map(m => ({{
                                query: m.query,
                                option: reviveFunctions(m.option, data)
                            }}))
                        }});
                    }} else {{
                        chart.setOption(panelOptions);
                    }}

                    // For last panel: run post-init JS
                    // Available vars: chart, chartDom, panelOptions (as baseOptions/options), data, mediaQueries
                    if (index === numPanels - 1) {{
                        const baseOptions = panelOptions;
                        const options = panelOptions;
                        {post_init_js}
                    }}
                }});

                // Handle window resize for all charts
                window.addEventListener('resize', function() {{
                    charts.forEach((item, index) => {{
                        const newHeight = calculatePanelHeight(item.options, numPanels);
                        item.dom.style.height = newHeight + 'px';
                        item.chart.resize();
                    }});
                }});

            }} catch (e) {{
                const firstPanel = document.getElementById('chart-0');
                if (firstPanel) {{
                    firstPanel.innerHTML =
                        '<div style="color: red; padding: 40px; font-size: 16px;">' +
                        '<strong>Error rendering chart:</strong><br><br>' + e.message + '</div>';
                }}
            }}
        }}

        // Wait for next frame to ensure CSS layout is calculated
        requestAnimationFrame(initCharts);
    </script>
</body>
</html>'''
        return request.make_response(
            html,
            headers={'Content-Type': 'text/html'}
        )

    def _render_error_page(self, title, error_message):
        """Render an error page."""
        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Error - {title}</title>
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
        .error-box h1 {{
            color: #dc3545;
            margin-bottom: 20px;
        }}
        .error-box p {{
            color: #666;
            line-height: 1.6;
        }}
        .error-box a {{
            color: #714B67;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="error-box">
        <h1>Error</h1>
        <p>{error_message}</p>
        <p><a href="javascript:history.back()">&#8592; Go Back</a></p>
    </div>
</body>
</html>'''
        return request.make_response(
            html,
            headers={'Content-Type': 'text/html'}
        )

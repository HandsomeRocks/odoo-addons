# -*- coding: utf-8 -*-
{
    'name': 'Odoo MCP Studio - AI React App, Module & EChart Builder',
    'version': '17.0.4.7.0',
    'category': 'Technical',
    'summary': '''AI React App, Module & EChart Builder — Powered by Remote MCP. Build web apps, data modules, dashboards & automate workflows with AI agents like Claude, Gemini, and ChatGPT.
    ai, artificial intelligence, llm, large language model, ai agent, ai assistant, ai copilot, mcp, model context protocol, remote mcp, remote mcp server,
    odoo ai, odoo mcp, odoo chatgpt, odoo claude, odoo gemini, odoo llm, odoo copilot, odoo artificial intelligence, odoo ai agent, odoo mcp studio,
    claude desktop, claude code, anthropic, gemini cli, chatgpt, gpt, copilot, cursor, windsurf, cline,
    oauth, oauth 2.1, api, rest api, json-rpc, xml-rpc, connector, integration, automation,
    echarts, apache echarts, dashboard, chart, graph, visualization, analytics, kpi, metrics, reports, 3d charts, globe, wordcloud,
    ai dashboard, ai analytics, ai dashboard builder, ai visualization, natural language, plain english, no code,
    ai app builder, ai web app builder, ai webapp builder, ai application builder, ai studio,
    ai module builder, ai module creator, data module, import module, base import module, custom module, module generator,
    react, web app, webapp, spa, single page application, multi page, routing, react router, tailwind, jsx, babel, pwa, progressive web app,
    business intelligence, bi, data analytics, data analysis, insights,
    orm, code search, audit log, security, access control, enterprise, compliance,
    sales, inventory, crm, project, stock, customer, product, invoices, orders,
    chatbot, bot, ai bot, nlp, prompt, prompt template,
    odoo17, odoo 17, remote, server, secure, token, pkce''',
    'description': """
Odoo MCP Studio - AI React App, Module & EChart Builder
===================

A complete Model Context Protocol (MCP) server implementation that enables
AI agents to securely connect and interact with your Odoo instance.

Key Features
------------
* Full OAuth 2.1 authentication with PKCE and token rotation
* Dynamic Client Registration (RFC 7591)
* 17 built-in MCP tools for data access, code execution, and source code search
* Per-tool user allowlists for granular access control
* Model and field-level access restrictions
* Comprehensive audit logging
* Prompt templates with argument substitution
* AI-generated interactive dashboards with Apache ECharts

AI Module Builder (NEW in 4.0)
------------------------------
* Create custom Odoo data modules through AI conversation
* Add models, fields, views, menus, security rules, and server actions via XML/CSV data files
* Automatic ZIP packaging and one-click installation via base_import_module
* Modules persist across restarts and appear in the standard Apps menu
* Full lifecycle management: create, package, install, update, and uninstall
* Download packaged ZIP files for backup or manual deployment

ECharts Dashboards
------------------
* Describe dashboards in natural language — AI builds them for you
* Powered by Apache ECharts for unlimited visualization possibilities
* Full data isolation support — AI generates code, execution stays on your server
* Share dashboards via public links or internal user/group sharing
* No configuration lock-in — pure Python data code and JSON chart options

Advanced ECharts (NEW in 2.1)
-----------------------------
* ECharts extensions: echarts-gl (3D/globes), wordcloud, liquidfill, stat
* Pre-init JavaScript: registerMap, registerTheme, registerTransform
* Post-init JavaScript: event handlers, drill-down, interactivity
* 3D Globe Command Center prompt template included

React Web Applications (NEW in 3.0)
------------------------------------
* Build full React web applications with multi-page routing
* React 19 via esm.sh with browser-native import maps
* Babel standalone for JSX compilation — no server build required
* React Router 6 with HashRouter (Odoo.sh compatible)
* Tailwind CSS for utility-first styling
* Custom API endpoints with Python handlers
* Share apps via public links or internal user/group sharing

Supported AI Clients
--------------------
* Claude Desktop & Claude Code CLI
* Gemini CLI
* ChatGPT
* Any Remote MCP-compatible client

Getting Started
---------------
1. Install the module
2. Navigate to MCP Server → Configuration
3. Add users to the MCP User group
4. Configure tool allowlists for write access
5. Connect your AI client using the /mcp endpoint
    """,
    'author': 'Codemarchant',
    'website': 'https://codemarchant.com',
    'support': 'support@codemarchant.com',
    'license': 'OPL-1',
    'price': 299.00,
    'currency': 'EUR',
    'depends': ['base', 'web', 'mail', 'base_import_module'],
    'data': [
        'security/mcp_security.xml',
        'security/ir.model.access.csv',
        'data/mcp_config_data.xml',
        'data/mcp_tag_data.xml',
        'data/mcp_crons.xml',
        'data/mcp_prompt_data.xml',
        'wizard/mcp_model_group_add_models_views.xml',
        'views/mcp_config_views.xml',
        'views/mcp_model_group_views.xml',
        'views/ir_model_views.xml',
        'views/mcp_api_key_views.xml',
        'views/mcp_oauth_client_views.xml',
        'views/mcp_execution_log_views.xml',
        'views/mcp_prompt_views.xml',
        'views/mcp_menu_views.xml',
        'views/mcp_echart_views.xml',
        'views/mcp_webapp_views.xml',
        'views/mcp_module_views.xml',
        'views/mcp_tag_views.xml',
        'views/mcp_docs.xml',
        'views/oauth_web_screen.xml',
        'views/res_users_views.xml',
    ],
    'images': ['static/description/banner.gif'],
    'installable': True,
    'application': True,
    'auto_install': False,
}

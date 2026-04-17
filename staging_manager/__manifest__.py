{
    "name": "Staging Manager",
    "version": "17.0.1.0.0",
    "category": "Tools",
    "summary": "Manage staging instances directly from Odoo",
    "description": """
        Connect to the Staging Manager API to create, monitor, and
        control staging instances without leaving Odoo.

        Features:
        - View all staging instances with live status
        - Create new instances from git branches
        - Start / Stop / Rebuild / Destroy instances
        - Run automated tests and view logs
        - Kanban board grouped by status
    """,
    "author": "Staging Manager",
    "depends": ["base"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/staging_instance_views.xml",
        "views/res_config_settings_views.xml",
        "wizard/create_instance_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": True,
    "license": "LGPL-3",
}

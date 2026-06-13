"""Dashboard presentation layer."""

from .overview import (
    build_dashboard_overview,
    dashboard_hidden_projects_path,
    hide_dashboard_project,
    load_hidden_projects,
    restore_dashboard_project,
)
from .view_model import DashboardViewModel

__all__ = [
    "DashboardViewModel",
    "build_dashboard_overview",
    "dashboard_hidden_projects_path",
    "hide_dashboard_project",
    "load_hidden_projects",
    "restore_dashboard_project",
]

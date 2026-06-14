"""Dashboard presentation layer."""

from .overview import (
    build_dashboard_overview,
    dashboard_hidden_projects_path,
    dashboard_hidden_tasks_path,
    hide_dashboard_project,
    hide_dashboard_task,
    load_hidden_projects,
    load_hidden_tasks,
    restore_dashboard_project,
    restore_dashboard_task,
)
from .view_model import DashboardViewModel

__all__ = [
    "DashboardViewModel",
    "build_dashboard_overview",
    "dashboard_hidden_projects_path",
    "dashboard_hidden_tasks_path",
    "hide_dashboard_project",
    "hide_dashboard_task",
    "load_hidden_projects",
    "load_hidden_tasks",
    "restore_dashboard_project",
    "restore_dashboard_task",
]

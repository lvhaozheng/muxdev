"""Skill governance layer public API.

The package keeps old muxdev call sites stable while splitting skill handling
into small modules: discovery, catalog, selection, activation, trust, locking,
installation, and evaluation.
"""

from .activation import activate_skill, resolve_active_skills
from .bindings import bind_skill, unbind_skill
from .catalog import SkillCatalog, SkillCatalogItem, build_skill_catalog
from .discovery import load_skills_config, scan_all_skills, scan_skills
from .evals import abtest_skill, eval_skill, score_skill
from .installer import SkillRecord, SkillRegistry, add_skill, export_skill, remove_skill, sync_skills
from .lock import verify_skill_lock, write_skill_lock
from .model import ActivatedSkill, SkillInfo, SkillPermissions, SkillSelection, TrustState
from .selector import select_skills
from .trust import set_skill_policy
from .validation import skill_doctor, skill_show, validate_skill_path

__all__ = [
    "ActivatedSkill",
    "SkillCatalog",
    "SkillCatalogItem",
    "SkillInfo",
    "SkillPermissions",
    "SkillRecord",
    "SkillRegistry",
    "SkillSelection",
    "TrustState",
    "abtest_skill",
    "activate_skill",
    "add_skill",
    "bind_skill",
    "build_skill_catalog",
    "eval_skill",
    "export_skill",
    "load_skills_config",
    "remove_skill",
    "resolve_active_skills",
    "scan_all_skills",
    "scan_skills",
    "score_skill",
    "select_skills",
    "set_skill_policy",
    "skill_doctor",
    "skill_show",
    "sync_skills",
    "unbind_skill",
    "validate_skill_path",
    "verify_skill_lock",
    "write_skill_lock",
]

"""On-disk storage for projects, recipes and source snapshots.

Both the web server and the Python API (``import frameloom``) read and write the
same FRAMELOOM_HOME folder (default ~/.frameloom), so every read checks the file's modification time and
reloads when another process saved a newer copy.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional

from .models import Project, Recipe

def default_home() -> str:
    # LOOM_HOME is the pre-1.0 name of the setting; still honoured.
    return os.environ.get("FRAMELOOM_HOME") or os.environ.get("LOOM_HOME") or os.path.join(os.path.expanduser("~"), ".frameloom")


def atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


class Store:
    def __init__(self, home: Optional[str] = None):
        self.home = home or default_home()
        self.projects_dir = os.path.join(self.home, "projects")
        self.recipes_dir = os.path.join(self.home, "recipes")
        self.snapshots_dir = os.path.join(self.home, "snapshots")
        self.projects: dict[str, Project] = {}
        self.recipes: dict[str, Recipe] = {}
        self._mtimes: dict[str, int] = {}

    # ----------------------------------------------------------- projects
    def _project_file(self, project_id: str) -> str:
        return os.path.join(self.projects_dir, project_id, "project.json")

    def get(self, project_id: str) -> Optional[Project]:
        if not project_id or "/" in project_id or project_id.startswith("."):
            return None
        path = self._project_file(project_id)
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            return self.projects.get(project_id)
        if project_id not in self.projects or self._mtimes.get(project_id) != mtime:
            with open(path) as f:
                self.projects[project_id] = Project.model_validate_json(f.read())
            self._mtimes[project_id] = mtime
        return self.projects[project_id]

    def save(self, project: Project) -> None:
        path = self._project_file(project.id)
        atomic_write(path, project.model_dump_json())
        self.projects[project.id] = project
        self._mtimes[project.id] = os.stat(path).st_mtime_ns

    def delete(self, project_id: str) -> None:
        self.projects.pop(project_id, None)
        self._mtimes.pop(project_id, None)
        project_dir = os.path.join(self.projects_dir, project_id)
        if os.path.isdir(project_dir):
            shutil.rmtree(project_dir)

    def all(self) -> list[Project]:
        if os.path.isdir(self.projects_dir):
            for pid in os.listdir(self.projects_dir):
                if os.path.exists(self._project_file(pid)):
                    try:
                        self.get(pid)
                    except Exception as e:  # a corrupt file shouldn't hide the others
                        print(f"Failed to load project {pid}: {e}")
        return list(self.projects.values())

    def find(self, ref: str) -> Project:
        """A project by id, id prefix, or (most recent) name."""
        project = self.get(ref)
        if project:
            return project
        candidates = [p for p in self.all() if p.id.startswith(ref) or p.name == ref]
        if not candidates:
            raise KeyError(f"No project '{ref}'")
        return max(candidates, key=lambda p: p.created_at)

    # ------------------------------------------------------------ recipes
    def save_recipe(self, recipe: Recipe) -> None:
        atomic_write(os.path.join(self.recipes_dir, f"{recipe.id}.json"), recipe.model_dump_json())
        self.recipes[recipe.id] = recipe

    def delete_recipe(self, recipe_id: str) -> None:
        self.recipes.pop(recipe_id, None)
        path = os.path.join(self.recipes_dir, f"{recipe_id}.json")
        if os.path.exists(path):
            os.remove(path)

    def load_recipes(self) -> list[Recipe]:
        if os.path.isdir(self.recipes_dir):
            for r_file in os.listdir(self.recipes_dir):
                if r_file.endswith(".json"):
                    try:
                        with open(os.path.join(self.recipes_dir, r_file)) as f:
                            recipe = Recipe.model_validate_json(f.read())
                        self.recipes[recipe.id] = recipe
                    except Exception as e:
                        print(f"Failed to load recipe {r_file}: {e}")
        return list(self.recipes.values())

    def find_recipe(self, ref: str) -> Recipe:
        self.load_recipes()
        if ref in self.recipes:
            return self.recipes[ref]
        matches = [r for r in self.recipes.values() if r.name == ref]
        if not matches:
            raise KeyError(f"No recipe '{ref}'")
        return matches[-1]

import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

import click

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DefaultGroup(click.Group):
    """`loom FILE` means `loom open FILE`; everything else is a subcommand."""

    def parse_args(self, ctx, args):
        if args and args[0] not in self.commands and args[0] not in ("--help", "-h"):
            args = ["open", *args]
        return super().parse_args(ctx, args)


@click.group(cls=DefaultGroup)
def cli():
    """Loom - Git for Dataframes.

    \b
    loom data.csv                      open a file in the app
    loom list                          projects, with versions and branches
    loom versions PROJECT              a project's named versions and metrics
    loom export PROJECT -v v2 -o x.parquet
    loom code PROJECT -v v2 --target pandas
    loom run RECIPE data.csv -o clean.parquet
    loom recipes                       saved recipes
    """


def _core():
    from backend.main import loom

    return loom


def _wait_for_server(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.2)
    return False


def _print_recipes():
    recipes = _core().store.load_recipes()
    if not recipes:
        click.echo("No recipes found.")
        return
    click.echo("Available Recipes:")
    for r in sorted(recipes, key=lambda r: r.name):
        click.echo(f"  - {r.name} ({len(r.operations)} steps)")


def _legacy_recipes_flag(ctx, param, value):
    if value and not ctx.resilient_parsing:
        _print_recipes()
        ctx.exit()


@cli.command("open", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.argument("filepath")
@click.option("--port", default=8000, show_default=True, help="Port to run the backend on.")
@click.option("--dev", is_flag=True, help="Open the Vite dev server (http://localhost:5173) instead.")
@click.option("--no-browser", is_flag=True, help="Start the server without opening a browser.")
@click.option("--sheet", default=None, help="Excel sheet to open.")
@click.option("--table", default=None, help="Database table to open (SQLite/DuckDB files).")
@click.option("--query", default=None, help="SQL query to run against a database file.")
@click.option("--separator", default=None, help="Column separator for delimited text files.")
@click.option("--format", "fmt", default=None, help="Force a file format (csv, tsv, parquet, excel, json, ...).")
@click.option("--recipes", is_flag=True, is_eager=True, expose_value=False, callback=_legacy_recipes_flag, help="List saved recipes.")
@click.pass_context
def open_cmd(ctx, filepath, port, dev, no_browser, sheet, table, query, separator, fmt):
    """Open FILEPATH (CSV, TSV, Parquet, Excel, JSON, Arrow, SQLite/DuckDB or a
    URL) in the app. Add --<recipe-name> to apply a saved recipe on open."""
    if filepath == "build-frontend":  # old spelling of `loom build-frontend`
        ctx.invoke(build_frontend)
        return

    import uvicorn

    from .sources import is_remote

    path = filepath if is_remote(filepath) else os.path.abspath(os.path.expanduser(filepath))
    if not is_remote(path) and not os.path.exists(path):
        click.echo(f"Error: File '{filepath}' does not exist.", err=True)
        ctx.exit(1)

    click.echo(f"Starting Loom for: {path}")
    recipe_name = next((arg[2:] for arg in ctx.args if arg.startswith("--")), None)
    options = {k: v for k, v in {"sheet_name": sheet, "table": table, "query": query,
                                 "separator": separator, "format": fmt}.items() if v}

    def open_browser():
        if not _wait_for_server(port):
            click.echo("Server did not start in time; open the app manually.", err=True)
            return
        params = {"path": path}
        if recipe_name:
            params["recipe_name"] = recipe_name
        if options:
            params["options"] = json.dumps(options)
        base_url = "http://localhost:5173" if dev else f"http://localhost:{port}"
        webbrowser.open(f"{base_url}/?{urllib.parse.urlencode(params)}")

    if not no_browser:
        threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, log_level="info")


@cli.command("list")
def list_cmd():
    """List projects (newest first)."""
    core = _core()
    projects = sorted(core.store.all(), key=lambda p: p.created_at, reverse=True)
    if not projects:
        click.echo("No projects yet. Open a file with: loom data.csv")
        return
    for p in projects:
        pin = "pinned" if p.snapshot_path else "unpinned"
        versions = ", ".join(p.versions) or "-"
        steps = len(core.path_nodes(p, core.head_node_id(p))) - 1
        click.echo(f"{p.id[:8]}  {p.name}  ({steps} steps, {len(p.branches)} branches, {pin})")
        click.echo(f"          source: {p.source_path}")
        click.echo(f"          versions: {versions}")


@cli.command("versions")
@click.argument("project")
def versions_cmd(project):
    """Show PROJECT's named versions with their metrics."""
    core = _core()
    p = core.store.find(project)
    if not p.versions:
        click.echo(f"{p.name} has no versions yet.")
        return
    for v in sorted(p.versions.values(), key=lambda v: v.created_at):
        metrics = "  ".join(f"{k}={val}" for k, val in v.metrics.items())
        click.echo(f"{v.name:<16} {v.created_at:%Y-%m-%d %H:%M}  {v.message}")
        if metrics:
            click.echo(f"{'':<16} {metrics}")


def _check_source(core, p, allow_changed):
    status = core.source_status(p)
    if status["status"] == "changed" and not allow_changed:
        raise click.ClickException(status["message"] + " Use --allow-changed-source to continue anyway.")
    if status["status"] == "missing":
        raise click.ClickException(status["message"])


@cli.command("export")
@click.argument("project")
@click.option("-v", "--version", "ref", default=None, help="Version, branch or step id (default: current).")
@click.option("-o", "--out", required=True, help="Output file (.csv, .parquet, .xlsx, .json, .ndjson, .arrow).")
@click.option("--format", "fmt", default=None, help="Output format if the extension doesn't say.")
@click.option("--allow-changed-source", is_flag=True, help="Export even if the source file changed.")
def export_cmd(project, ref, out, fmt, allow_changed_source):
    """Write a version of PROJECT's data to a file."""
    from loom.api import LoomProject

    core = _core()
    p = core.store.find(project)
    _check_source(core, p, allow_changed_source)
    try:
        LoomProject(p.id).export(out, ref, fmt, allow_changed_source=allow_changed_source)
    except (KeyError, ValueError) as e:
        raise click.ClickException(str(e.args[0] if isinstance(e, KeyError) else e))
    click.echo(f"Wrote {out}")


@cli.command("code")
@click.argument("project")
@click.option("-v", "--version", "ref", default=None, help="Version, branch or step id (default: current).")
@click.option("--target", type=click.Choice(["polars", "pandas"]), default="polars", show_default=True)
def code_cmd(project, ref, target):
    """Print a Python script that reproduces a version of PROJECT."""
    from loom.api import LoomProject

    p = _core().store.find(project)
    try:
        click.echo(LoomProject(p.id).code(ref, target), nl=False)
    except KeyError as e:
        raise click.ClickException(str(e.args[0]))


@cli.command("run")
@click.argument("recipe")
@click.argument("filepath")
@click.option("-o", "--out", required=True, help="Where to write the result.")
@click.option("--force", is_flag=True, help="Run even if the file doesn't match what the recipe expects.")
@click.option("--save-project", is_flag=True, help="Also keep the run as a Loom project.")
@click.option("--sheet", default=None)
@click.option("--table", default=None)
@click.option("--separator", default=None)
def run_cmd(recipe, filepath, out, force, save_project, sheet, table, separator):
    """Apply RECIPE to FILEPATH without opening the app (for scripts and cron)."""
    from . import sources
    from .core import HistoryError

    core = _core()
    try:
        r = core.store.find_recipe(recipe)
    except KeyError as e:
        raise click.ClickException(str(e.args[0]))
    options = {k: v for k, v in {"sheet_name": sheet, "table": table, "separator": separator}.items() if v}
    try:
        project, warnings = core.apply_recipe(r, filepath, options, force=force, save=save_project, snapshot=save_project or None)
    except (HistoryError, FileNotFoundError) as e:
        raise click.ClickException(str(e))
    for w in warnings:
        click.echo(f"warning: {w}", err=True)
    if project is None:
        raise click.ClickException("The file doesn't match the recipe (see warnings). Use --force to run anyway.")
    fmt = {".csv": "csv", ".tsv": "tsv", ".parquet": "parquet", ".pq": "parquet", ".xlsx": "xlsx", ".json": "json",
           ".ndjson": "ndjson", ".jsonl": "ndjson", ".arrow": "ipc"}.get(os.path.splitext(out)[1].lower())
    if not fmt:
        raise click.ClickException("Can't tell the output format from the file name")
    sources.write_export(core.engine.build_plan(project, project.branches["main"].head_node_id), fmt, out)
    click.echo(f"Applied '{r.name}' ({len(r.operations)} steps) -> {out}" + (f"  [project {project.id[:8]}]" if save_project else ""))


@cli.command("recipes")
def recipes_cmd():
    """List saved recipes."""
    _print_recipes()


@cli.command("build-frontend")
def build_frontend():
    """Install and build the web UI (needs Node.js)."""
    frontend_dir = os.path.join(BASE_DIR, "frontend")
    click.echo("Building frontend...")
    subprocess.run(["npm", "install"], cwd=frontend_dir, check=True)
    subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)
    click.echo("Frontend built successfully.")


def main():
    cli(prog_name="loom")


if __name__ == "__main__":
    sys.exit(main())

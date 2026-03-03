import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

import click
import uvicorn

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _recipes_dir() -> str:
    loom_dir = os.environ.get("LOOM_HOME") or os.path.join(BASE_DIR, ".loom")
    return os.path.join(loom_dir, "recipes")


def print_recipes(ctx, param, value):
    if not value or ctx.resilient_parsing:
        return
    recipes_dir = _recipes_dir()
    files = [f for f in os.listdir(recipes_dir) if f.endswith(".json")] if os.path.isdir(recipes_dir) else []
    if not files:
        click.echo("No recipes found.")
        ctx.exit()

    click.echo("Available Recipes:")
    for r_file in sorted(files):
        try:
            with open(os.path.join(recipes_dir, r_file), "r") as f:
                data = json.load(f)
            click.echo(f"  - {data.get('name', 'Unknown')} ({len(data.get('operations', []))} steps)")
        except (OSError, json.JSONDecodeError) as e:
            click.echo(f"  ! Could not read {r_file}: {e}", err=True)
    ctx.exit()


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


@click.command(context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.argument("filepath")
@click.option("--port", default=8000, show_default=True, help="Port to run the backend on.")
@click.option("--dev", is_flag=True, help="Open the Vite dev server (http://localhost:5173) instead.")
@click.option("--no-browser", is_flag=True, help="Start the server without opening a browser.")
@click.option("--sheet", default=None, help="Excel sheet to open.")
@click.option("--table", default=None, help="Database table to open (SQLite/DuckDB files).")
@click.option("--query", default=None, help="SQL query to run against a database file.")
@click.option("--separator", default=None, help="Column separator for delimited text files.")
@click.option("--format", "fmt", default=None, help="Force a file format (csv, tsv, parquet, excel, json, ...).")
@click.option("--recipes", is_flag=True, is_eager=True, expose_value=False, callback=print_recipes, help="List all saved recipes.")
@click.pass_context
def cli(ctx, filepath, port, dev, no_browser, sheet, table, query, separator, fmt):
    """Loom - Git for Dataframes

    Open FILEPATH (CSV, TSV, Parquet, Excel, JSON, Arrow, SQLite/DuckDB or an
    http/s3 URL). Add --<recipe-name> to apply a saved recipe on open.
    """
    if filepath == "build-frontend":
        frontend_dir = os.path.join(BASE_DIR, "frontend")
        click.echo("Building frontend...")
        subprocess.run(["npm", "install"], cwd=frontend_dir, check=True)
        subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)
        click.echo("Frontend built successfully.")
        return

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


if __name__ == "__main__":
    cli()

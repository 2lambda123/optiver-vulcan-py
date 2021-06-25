import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List

import build
import build.env
import click
import packaging.version
import tomlkit
from pkg_resources import Requirement

from vulcan import Vulcan, flatten_reqs
from vulcan.build_backend import get_virtualenv_python, install_develop
from vulcan.builder import resolve_deps

pass_vulcan = click.make_pass_decorator(Vulcan)


@click.group()
@click.pass_context
def main(ctx: click.Context) -> None:
    ctx.obj = Vulcan.from_source(Path().absolute())


def build_shiv_apps(from_dist: str, vulcan: Vulcan, outdir: Path) -> List[Path]:
    results = []
    for app in vulcan.shiv_options:
        try:
            if app.with_extras:
                dist = f'{from_dist}[{",".join(app.with_extras)}]'
            else:
                dist = from_dist
            cmd = [sys.executable, '-m', 'shiv', dist, '-o', str(outdir / app.bin_name)]
            if app.console_script:
                cmd += ['-c', app.console_script]
            if app.entry_point:
                cmd += ['-e', app.entry_point]
            if app.interpreter:
                cmd += ['-p', app.interpreter]
            if app.extra_args:
                cmd += shlex.split(app.extra_args)
            res = subprocess.run(cmd)
            if res.returncode != 0:
                raise SystemExit(res.returncode)
            results.append(outdir / app.bin_name)
        except KeyError as e:
            raise KeyError('missing config value in pyproject.toml: {e}') from e
    return results


@main.command(name='build')
@click.option('--outdir', '-o', default='dist/', type=Path)
@click.option('--lock/--no-lock', '_lock', default=True)
@click.option('--wheel', is_flag=True, default=False)
@click.option('--sdist', is_flag=True, default=False)
@click.option('--shiv', is_flag=True, default=False)
@pass_vulcan
def build_out(config: Vulcan, outdir: Path, _lock: bool, wheel: bool, sdist: bool, shiv: bool) -> None:
    "Create wheels, sdists, and shiv executables"
    # for ease of use
    if len([v for v in (shiv, wheel, sdist) if v]) != 1:
        raise click.UsageError("Must specify exactly 1 of --shiv, --wheel, or --sdist")

    should_lock = _lock and not config.no_lock

    project = build.ProjectBuilder('.')
    outdir.mkdir(exist_ok=True)
    config_settings = {}
    if not _lock:
        config_settings['no-lock'] = 'true'
    if sdist:
        dist = project.build('sdist', str(outdir), config_settings=config_settings)
    elif wheel or shiv:
        if shiv and not should_lock:
            raise click.UsageError("May not specify both --shiv and --no-lock; shiv builds must be locked")
        dist = project.build('wheel', str(outdir), config_settings=config_settings)
    else:
        assert False, 'unreachable because dist_types is required'
    if shiv:
        try:
            build_shiv_apps(dist, config, outdir)
        finally:
            os.remove(dist)


@main.command()
@pass_vulcan
def lock(config: Vulcan) -> None:
    "Generate and update lockfile"
    install_requires, extras_require = resolve_deps(flatten_reqs(config.configured_dependencies),
                                                    config.configured_extras or {},
                                                    config.python_lock_with)
    doc = tomlkit.document()
    doc['install_requires'] = tomlkit.array(install_requires).multiline(True)  # type: ignore
    doc['extras_require'] = {k: tomlkit.array(v).multiline(True)   # type: ignore
                             for k, v in extras_require.items()}
    with open(config.lockfile, 'w+') as f:
        f.write(tomlkit.dumps(doc))


@main.command()
@click.argument('req', type=Requirement.parse)
@click.option('--lock/--no-lock', '_lock', default=True)
@pass_vulcan  # order matters, closest the the function definition comes first
@click.pass_context
def add(ctx: click.Context, config: Vulcan, req: Requirement, _lock: bool) -> None:
    "Add new top-level dependency and regenerate lockfile"
    name: str = req.name  # type: ignore
    if req.extras:
        name = f'{name}[{",".join(req.extras)}]'
    try:
        venv_python = get_virtualenv_python()
    except RuntimeError:
        exit("Must be in a virtualenv to use `vulcan add`")
    subprocess.check_call([venv_python, '-m', 'pip', 'install', str(req)])
    if req.specifier:  # type: ignore
        # if the user gave a version spec, we blindly take that
        version = str(req.specifier)  # type: ignore
    else:
        # otherwise, we take a freeze to see what was actually installed
        freeze = subprocess.check_output([venv_python, '-m', 'pip', 'freeze'], encoding='utf-8').strip()
        try:
            # try and find the thing we just added
            line = next(ln for ln in freeze.split('\n') if ln.startswith(req.name))  # type: ignore
            # and parse it to a version
            spec = packaging.version.parse(str(Requirement.parse(line.strip()).specifier  # type: ignore
                                               )[2:])  # remove the == at the start
            if isinstance(spec, packaging.version.LegacyVersion):
                # this will raise a DeprecationWarning as well, so it will yell at user for us.
                version = ''
            else:
                version = f'~={spec.major}.{spec.minor}'
        except StopIteration:
            # failed to find the thing we just installed, give up.
            version = ''
    with open('pyproject.toml') as f:
        parse = tomlkit.parse(f.read())
    deps = parse['tool']['vulcan'].setdefault('dependencies', tomlkit.table())  # type: ignore
    deps[name] = version  # type: ignore
    with open('pyproject.toml', 'w+') as f:
        f.write(tomlkit.dumps(parse))
    if not config.no_lock and _lock:
        ctx.invoke(lock)


@main.command()
def develop() -> None:
    "Install project into current virtualenv as editable"
    install_develop()


if __name__ == '__main__':
    main()

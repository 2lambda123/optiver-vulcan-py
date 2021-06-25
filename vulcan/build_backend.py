import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import tomlkit

from vulcan import Vulcan, flatten_reqs

# importing setuptools here rather than at point of use forces user to specify setuptools in their
# [build-system][requires] section
try:
    import setuptools  # type: ignore
except ImportError as e:
    raise ImportError(str(e) + '\nPlease add setuptools to [build-system] requires in pyproject.toml') from e


try:
    # Remove this whole  block when https://github.com/psf/fundable-packaging-improvements/issues/25 is
    # finalized
    from ppsetuptools.ppsetuptools import _parse_kwargs  # type: ignore

    def _filter_nones(vals_dict: Dict[str, Optional[Any]]) -> Dict[str, Any]:
        return {k: v for k, v in vals_dict.items() if v is not None}

    def setup(**kwargs: Any) -> Any:

        with open('pyproject.toml', 'r') as pptoml:
            pyproject_data = dict(tomlkit.parse(pptoml.read()))

        if 'project' in pyproject_data:
            if 'dependencies' in pyproject_data['project']:
                raise RuntimeError("May not use [project]:dependencies key with vulcan")
            if 'optional-dependencies' in pyproject_data['project']:
                raise RuntimeError("May not use [project]:optional-dependencies key with vulcan")
            parsed_kwargs = _parse_kwargs(pyproject_data['project'], '.')
            parsed_kwargs.update(_filter_nones(kwargs))
            parsed_kwargs = _filter_nones(parsed_kwargs)
            # ppsetuptools doesn't handle entry points correctly
            if 'scripts' in parsed_kwargs:
                if 'entry_points' not in parsed_kwargs:
                    parsed_kwargs['entry_points'] = {}
                parsed_kwargs['entry_points']['console_scripts'] = parsed_kwargs['scripts']
                del parsed_kwargs['scripts']
            if 'gui-scripts' in parsed_kwargs:
                parsed_kwargs['entry_points']['gui_scripts'] = parsed_kwargs['gui-scripts']
                del parsed_kwargs['gui-scripts']
            if 'entry_points' in parsed_kwargs:
                for ep_group in list(parsed_kwargs['entry_points']):
                    parsed_kwargs['entry_points'][ep_group] = [
                        f'{k}={v}' for k, v in parsed_kwargs['entry_points'][ep_group].items()]
            return setuptools.setup(**parsed_kwargs)
        else:
            return setuptools.setup(**_filter_nones(kwargs))


except ImportError:
    setup = setuptools.setup

__all__ = ['build_wheel',
           'build_sdist']


@contextmanager
def patch_argv(argv: List[str]) -> Generator[None, None, None]:
    old_argv = sys.argv[:]
    sys.argv = [sys.argv[0]] + argv
    yield
    sys.argv = old_argv


def build(outdir: str, config_settings: Dict[str, str] = None) -> str:
    config = Vulcan.from_source(Path().absolute())
    options = config.metadata.asdict()
    if config.no_lock or (config_settings and config_settings.get('no-lock') == 'true'):
        options['install_requires'] = flatten_reqs(config.configured_dependencies)
        options['extras_require'] = config.configured_extras

    # https://setuptools.readthedocs.io/en/latest/userguide/keywords.html
    # https://docs.python.org/3/distutils/apiref.html
    dist = setup(**options, include_package_data=True)
    rel_dist = Path(dist.dist_files[0][-1])
    shutil.move(str(rel_dist), Path(outdir) / rel_dist.name)
    return rel_dist.name


def build_wheel(wheel_directory: str, config_settings: Dict[str, str] = None,
                metadata_directory: str = None) -> str:
    with patch_argv(['bdist_wheel']):
        return build(wheel_directory, config_settings)


def build_sdist(sdist_directory: str,
                config_settings: Dict[str, str] = None,
                ) -> str:
    with patch_argv(['sdist']):
        return build(sdist_directory, config_settings)


def get_virtualenv_python() -> Path:
    virtual_env = os.environ.get('VIRTUAL_ENV')
    if virtual_env is None:
        raise RuntimeError("No virtualenv active")
    return Path(virtual_env, 'bin', 'python')


# not part of PEP-517, but very useful to have
def install_develop() -> None:
    config = Vulcan.from_source(Path().absolute())
    options = config.metadata.asdict()

    if config.no_lock:
        options['install_requires'] = flatten_reqs(config.configured_dependencies)
        options['extras_require'] = config.configured_extras

    try:
        virtual_env = get_virtualenv_python()
    except RuntimeError:
        exit('may not use vulcan develop outside of a virtualenv')

    setup = Path('setup.py')
    if setup.exists():
        exit('may not use vulcan develop when setup.py is present')
    try:
        with tempfile.NamedTemporaryFile(suffix='.json', mode="w+") as mdata_file:
            mdata_file.write(json.dumps(options))
            mdata_file.flush()
            with setup.open('w+') as setup_file:
                setup_file.write(f"""\
from vulcan.build_backend import setup
import json, pathlib
setup(**json.load(pathlib.Path('{mdata_file.name}').open()))
""")
            subprocess.check_call([
                virtual_env, '-m', 'pip', 'install', '-e', Path().absolute()])
    finally:
        setup.unlink()


# tox requires these two fro some reason :(
def get_requires_for_build_sdist(config_settings: Dict[str, str] = None) -> List[str]:
    return []


def get_requires_for_build_wheel(config_settings: Dict[str, str] = None) -> List[str]:
    return []

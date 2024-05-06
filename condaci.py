#!/usr/bin/env python
import subprocess
import glob
import os
import contextlib
import shutil
import os.path as p
from functools import partial
import platform as stdplatform
import uuid
import sys
from pprint import pprint

VS9_PY_VERS = ['2.7']
VS10_PY_VERS = ['3.3', '3.4']
VS14_PY_VERS = ['3.5', '3.6', '3.7', '3.8', '3.9', '3.10', '3.11']
SUPPORTED_PY_VERS = VS9_PY_VERS + VS10_PY_VERS + VS14_PY_VERS

SUPPORTED_ERR_MSG = 'FATAL: Python version not supported, must be one of {}'.format(
    SUPPORTED_PY_VERS)

PROGRAM_FILES = os.environ.get('PROGRAMFILES(x86)', os.environ.get('PROGRAMFILES', ''))
VS2008_PATH = os.path.join(PROGRAM_FILES, 'Microsoft Visual Studio 9.0')
VS2008_BIN_PATH = os.path.join(VS2008_PATH, 'VC', 'bin')
VS2010_PATH = os.path.join(PROGRAM_FILES, 'Microsoft Visual Studio 10.0')
VS2010_BIN_PATH = os.path.join(VS2010_PATH, 'VC', 'bin')
VS2010_AMD64_VCVARS_CMD = r'CALL "C:\Program Files\Microsoft SDKs\Windows\v7.1\Bin\SetEnv.cmd" /x64 /Release'

# a random string we can use for the miniconda installer
# (to avoid name collisions)
RANDOM_UUID = uuid.uuid4()

# -------------------------------- STATE ------------------------------------ #

# Key globals that need to be set for the rest of the script.
PYTHON_VERSION = None
PYTHON_VERSION_NO_DOT = None
BINSTAR_USER = None
BINSTAR_KEY = None
PYPI_USER = None
PYPI_PASSWORD = None
PYPI_TEST_USER = None
PYPI_TEST_PASSWORD = None
ARCH = None

# Env variables that we need to be careful to purge (so they aren't
# divulged by accident)
SECRET_ENVS = ['BINSTAR_KEY', 'PYPI_PASSWORD', 'PYPI_TEST_PASSWORD']


def set_globals_from_environ(verbose=True):
    global PYTHON_VERSION, BINSTAR_KEY, BINSTAR_USER, PYTHON_VERSION_NO_DOT
    global PYPI_USER, PYPI_PASSWORD, PYPI_TEST_USER, PYPI_TEST_PASSWORD, ARCH

    if not (is_on_appveyor() or is_on_travis() or is_on_circleci()):
        raise ValueError('FATAL: Unknown CI system.')

    PYTHON_VERSION = os.environ.get('PYTHON_VERSION')
    # ARCH or PLATFORM - PLATFORM on Appveyor
    if 'ARCH' in os.environ or 'PLATFORM' in os.environ:
        ARCH = os.environ.get('ARCH', os.environ.get('PLATFORM'))
        arch_origin = 'Environment'
    else:
        # If we weren't given the ARCH variable (as on Jenkins) then predict
        # the architecture from the Python version (x86 or x64)
        ARCH = python_arch()
        arch_origin = 'Python'
    BINSTAR_USER = os.environ.get('BINSTAR_USER')
    BINSTAR_KEY = os.environ.get('BINSTAR_KEY')
    PYPI_USER = os.environ.get('PYPI_USER')
    PYPI_PASSWORD = os.environ.get('PYPI_PASSWORD')
    PYPI_TEST_USER = os.environ.get('PYPI_TEST_USER')
    PYPI_TEST_PASSWORD = os.environ.get('PYPI_TEST_PASSWORD')

    if verbose:
        print('Environment variables extracted:')
        print('  PYTHON_VERSION:                    {}'.format(PYTHON_VERSION))
        print('  ARCH:                              {} - ({})'.format(ARCH, arch_origin))
        print('  BINSTAR_USER:                      {}'.format(BINSTAR_USER))
        print('  BINSTAR_KEY:                       {}'.format('*****' if BINSTAR_KEY
                                                                          is not None else '-'))
        print('  PYPI_USER:                         {}'.format(PYPI_USER))
        print('  PYPI_PASSWORD:                     {}'.format('*****' if PYPI_PASSWORD
                                                                          is not None else '-'))
        print('  PYPI_TEST_USER:                    {}'.format(PYPI_TEST_USER))
        print('  PYPI_TEST_PASSWORD:                {}'.format('*****' if PYPI_TEST_PASSWORD
                                                                          is not None else '-'))
        print('  PYPI_SDIST_UPLOAD_PYTHON_VERSION:  {}'.format(PYPI_SDIST_UPLOAD_PYTHON_VERSION))

    if PYTHON_VERSION is None:
        raise ValueError('FATAL: PYTHON_VERSION is not set.')
    if PYTHON_VERSION not in SUPPORTED_PY_VERS:
        raise ValueError("FATAL: PYTHON_VERSION '{}' is invalid - must be "
                         "one of {}".format(PYTHON_VERSION, SUPPORTED_PY_VERS))
    if ARCH is None:
        raise ValueError('FATAL: ARCH is not set.')

    # Required when setting Python version in conda
    PYTHON_VERSION_NO_DOT = PYTHON_VERSION.replace('.', '')


# ------------------------------ UTILITIES ---------------------------------- #


class FakeSink(object):
    def write(self, *args, **kwargs):
        pass

    def flush(self, *args, **kwargs):
        pass


@contextlib.contextmanager
def suppress_stdout(include_stderr=False):
    cached_stdout = sys.stdout
    sys.stdout = FakeSink()
    if include_stderr:
        cached_stderr = sys.stderr
        sys.stderr = FakeSink()

    yield

    sys.stdout = cached_stdout
    if include_stderr:
        sys.stderr = cached_stderr


# forward stderr to stdout
check = partial(subprocess.check_call, stderr=subprocess.STDOUT)


def execute(cmd, verbose=True, env_additions=None):
    r""" Runs a command, printing the command and it's output to screen.
    """
    env_for_p = os.environ.copy()
    if env_additions is not None:
        env_for_p.update(env_additions)
    if verbose:
        print('> {}'.format(' '.join(cmd)))
        if env_additions is not None:
            print('Additional environment variables: '
                  '{}'.format(', '.join(['{}={}'.format(k, v)
                                         for k, v in env_additions.items()])))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env_for_p)
    sentinel = ''
    if sys.version_info.major == 3:
        sentinel = b''
    for line in iter(proc.stdout.readline, sentinel):
        if verbose:
            if sys.version_info.major == 3:
                # convert bytes to string
                line = line.decode("utf-8")
            sys.stdout.write(line)
            sys.stdout.flush()
    output = proc.communicate()[0]
    if proc.returncode == 0:
        return
    else:
        e = subprocess.CalledProcessError(proc.returncode, cmd, output=output)
        print(' -> {}'.format(e.output))
        raise e


def execute_sequence(*cmds, **kwargs):
    r""" Execute a sequence of commands. If any fails, display an error.
    """
    verbose = kwargs.get('verbose', True)
    for cmd in cmds:
        execute(cmd, verbose)


def download_file(url, path_to_download):
    f = get_url(url)
    with open(path_to_download, "wb") as fp:
        fp.write(f.read())
    fp.close()


def dirs_containing_file(fname, root=os.curdir):
    for path, dirs, files in os.walk(p.abspath(root)):
        if fname in files:
            yield path


def host_platform():
    return stdplatform.system()


def is_windows():
    return host_platform() == 'Windows'


def python_arch():
    # We care about the Python architecture, not the OS.
    return 'x64' if sys.maxsize > 2 ** 32 else 'x86'


# ------------------------ MINICONDA INTEGRATION ---------------------------- #

def url_for_platform_version(platform, arch):
    # Always install Miniconda3
    version = 'latest'
    base_url = 'https://repo.continuum.io/miniconda/Miniconda3'
    platform_str = {'Linux': 'Linux',
                    'Darwin': 'MacOSX',
                    'Windows': 'Windows'}
    arch_str = {'x64': 'x86_64',
                'x86': 'x86'}
    ext = {'Linux': '.sh',
           'Darwin': '.sh',
           'Windows': '.exe'}

    return '-'.join([base_url, version,
                     platform_str[platform],
                     arch_str[arch]]) + ext[platform]


def appveyor_miniconda_dir():
    # We always prefer the Miniconda3 version
    conda_dir = r'C:\Miniconda36'

    if ARCH == 'x64':
        conda_dir += '-x64'

    return conda_dir


def travis_miniconda_dir():
    return p.expanduser('~/miniconda')


circleci_miniconda_dir = travis_miniconda_dir


def temp_installer_path():
    # we need a place to download the miniconda installer to. use a random
    # string for the filename to avoid collisions, but choose the dir based
    # on platform
    return (r'C:\{}.exe'.format(RANDOM_UUID) if is_windows()
            else p.expanduser('~/{}.sh'.format(RANDOM_UUID)))


def miniconda_dir():
    # the directory where miniconda will be installed to/is
    if is_on_appveyor():
        path = appveyor_miniconda_dir()
    elif is_on_travis():
        path = travis_miniconda_dir()
    elif is_on_circleci():
        path = circleci_miniconda_dir()
    else:
        assert False, "Unknown CI"

    return path


# the script directory inside a miniconda install varies based on platform
def miniconda_script_dir_name():
    return 'Scripts' if is_windows() else 'bin'


# handles to binaries from a miniconda install
exec_ext = '.exe' if is_windows() else ''
miniconda_script_dir = lambda mc: p.join(mc, miniconda_script_dir_name())
miniconda_conda_bld_dir = lambda mc: p.join(mc, 'conda-bld')
conda = lambda mc: p.join(miniconda_script_dir(mc), 'conda' + exec_ext)
python = lambda mc: p.join(miniconda_script_dir(mc), 'python' + exec_ext)
twine = lambda mc: p.join(miniconda_script_dir(mc), 'twine' + exec_ext)
binstar = lambda mc: p.join(miniconda_script_dir(mc), 'anaconda' + exec_ext)


def unique_path_matching_glob(path_with_glob):
    possible_paths = glob.glob(path_with_glob)
    if len(possible_paths) != 1:
        raise ValueError("Couldn't find unique path matching glob '{}' - "
                         "found {}: {}".format(path_with_glob,
                                               len(possible_paths),
                                               possible_paths))
    return possible_paths[0]


def get_dirty_work_dir(mc):
    return unique_path_matching_glob(p.join(miniconda_conda_bld_dir(mc),
                                            'work_moved_*'))


def acquire_miniconda(url, path_to_download):
    print('Downloading miniconda from {} to {}'.format(url, path_to_download))
    download_file(url, path_to_download)


def install_miniconda(path_to_installer, path_to_install):
    print('Installing miniconda to {}'.format(path_to_install))
    if is_windows():
        execute([path_to_installer, '/InstallationType=AllUsers',
                 '/AddToPath=0', '/RegisterPath=1', '/NoRegistry=1',
                 '/S', '/D={}'.format(path_to_install)])
    else:
        execute(['chmod', '+x', path_to_installer])
        execute([path_to_installer, '-b', '-p', path_to_install])


def setup_miniconda(installation_path, binstar_user=None, extra_channels=None):
    conda_cmd = conda(installation_path)
    if os.path.exists(conda_cmd):
        print('conda is already setup at {}'.format(installation_path))
    else:
        print('No existing conda install detected at {}'.format(installation_path))
        url = url_for_platform_version(host_platform(), ARCH)
        print('Setting up miniconda from URL {}'.format(url))
        print("(Installing to '{}')".format(installation_path))
        acquire_miniconda(url, temp_installer_path())
        install_miniconda(temp_installer_path(), installation_path)
        # delete the installer now we are done
        os.unlink(temp_installer_path())
    cmds = [[conda_cmd, 'update', '-q', '--yes', 'conda'],
            [conda_cmd, 'install', '-q', '--yes', 'conda-build', 'conda-verify',
             'jinja2', 'ripgrep', 'anaconda-client', 'twine']]
    root_config = os.path.join(installation_path, '.condarc')
    if os.path.exists(root_config):
        print('existing root config at present at {} - removing'.format(root_config))
        os.unlink(root_config)
    if binstar_user is not None:
        print("(adding user channel '{}' for dependencies to root config)".format(binstar_user))
        cmds.append([conda_cmd, 'config', '--system', '--add', 'channels', binstar_user])
    else:
        print('No user channels have been configured (all dependencies have to '
              'be sourced from anaconda)')
    if extra_channels is not None:
        # Loop in reverse order to ensure the first channel in the list has
        # highest precedence
        for c in extra_channels[::-1]:
            print("adding extra channel '{}' for dependencies to root config".format(c))
            cmds.append([conda_cmd, 'config', '--system', '--add', 'channels', c])
    execute_sequence(*cmds)


# ------------------------ CONDA BUILD INTEGRATION -------------------------- #

def get_conda_build_path_v3(recipe_dir):
    with suppress_stdout(include_stderr=True):
        from conda_build.config import Config
        from conda_build.api import get_output_file_paths
        c = Config(variant={'python': PYTHON_VERSION})
        return get_output_file_paths(recipe_dir, no_download_source=True,
                                     config=c)[0]


def get_conda_build_path_v1_v2(recipe_dir):
    from conda_build.metadata import MetaData
    from conda_build.build import bldpkg_path
    m = MetaData(recipe_dir)
    fname = bldpkg_path(m)
    return fname.strip()


def get_conda_build_path(recipe_dir):
    from distutils.version import LooseVersion
    import conda_build
    cb_ver = LooseVersion(conda_build.__version__)
    if cb_ver < '3':
        return get_conda_build_path_v1_v2(recipe_dir)
    else:  # cb_ver >= 3
        if cb_ver >= '4':
            # conda-build 4 is unreleased at the time of commit so it is unknown
            # how stable this API is. Fallback to the v3 API and hope but
            # otherwise just signal that there may be an issue
            print('WARNING: conda-build version "{}" is untested with '
                  'condaci - there may be failures'.format(cb_ver))
        return get_conda_build_path_v3(recipe_dir)


def windows_setup_compiler():
    if PYTHON_VERSION in VS9_PY_VERS and ARCH == 'x64':
        VS2008_AMD64_PATH = os.path.join(VS2008_BIN_PATH, 'amd64')
        if not os.path.exists(VS2008_AMD64_PATH):
            os.makedirs(VS2008_AMD64_PATH)
        VCVARS64_PATH = os.path.join(VS2008_BIN_PATH, 'vcvars64.bat')
        VCVARSAMD64_PATH = os.path.join(VS2008_AMD64_PATH, 'vcvarsamd64.bat')
        if not os.path.exists(VCVARS64_PATH):
            print("Unable to find '{}' - skipping fix for VS2008 64-bit.".format(
                VCVARS64_PATH))
        else:
            print("Copying '{}' to '{}' to fix VS2008 64-bit configuration.".format(
                VCVARS64_PATH, VCVARSAMD64_PATH))
            shutil.copyfile(VCVARS64_PATH, VCVARSAMD64_PATH)
    # Python 3.3 or 3.4
    elif PYTHON_VERSION in VS10_PY_VERS and ARCH == 'x64':
        VS2010_AMD64_PATH = os.path.join(VS2010_BIN_PATH, 'amd64')
        if not os.path.exists(VS2010_AMD64_PATH):
            os.makedirs(VS2010_AMD64_PATH)
        VS2010_AMD64_VCVARS_PATH = os.path.join(VS2010_AMD64_PATH,
                                                'vcvars64.bat')
        print("Writing '{}' to '{}' to fix VS2010 64-bit configuration.".format(
            VS2010_AMD64_VCVARS_CMD, VS2010_AMD64_VCVARS_PATH))
        with open(VS2010_AMD64_VCVARS_PATH, 'w') as f:
            f.write(VS2010_AMD64_VCVARS_CMD)


def build_conda_package(mc, path, binstar_user=None):
    print('Building package at path {}'.format(path))
    print('Setting CONDA_PY environment variable to {}'.format(
        PYTHON_VERSION_NO_DOT))
    os.environ['CONDA_PY'] = PYTHON_VERSION_NO_DOT

    v = get_version(path)
    print('Detected version: {}'.format(v))
    print('Setting CONDACI_VERSION environment variable to {}'.format(v))
    os.environ['CONDACI_VERSION'] = v

    # we want to add the master channel when doing dev builds to source our
    # other dev dependencies
    if not (is_release_tag(v) or is_rc_tag(v)):
        print('building a non-release non-RC build - adding master channel.')
        if binstar_user is None:
            print('warning - no binstar user provided - cannot add master channel')
        else:
            execute([conda(mc), 'config', '--system', '--add', 'channels',
                     binstar_user + '/channel/master'])
    else:
        print('building a RC or tag release - no master channel added.')

    for key in SECRET_ENVS:
        if key in os.environ:
            print('found {} in environment - deleting to '
                  'stop vcvarsall from telling the world'.format(key))
            del os.environ[key]

    if is_windows():
        # Before building the package, we may need to edit the environment a bit
        # to handle the nightmare that is Visual Studio compilation
        windows_setup_compiler()

    # Always purge the conda-bld dir before this build (so we can unambiguously
    # find it after the build if we need to).
    execute([conda(mc), 'build', 'purge-all'])

    # Also clean all conda caches to remove previous build artifacts
    execute([conda(mc), 'clean', '--all', '--yes'])

    # Note the '--dirty' arg so we can inspect this build dir afterwards
    # --no-build-id ensures that we get b_env style folder structures
    execute([conda(mc), 'build', '-q', path,
             '--py={}'.format(PYTHON_VERSION_NO_DOT), '--dirty',
             '--no-build-id'])


# ------------------------- VERSIONING INTEGRATION -------------------------- #

# versions that match up to master changes (anything after a '+')
same_version_different_build = lambda v1, v2: v2.startswith(v1.split('+')[0])


def versions_from_versioneer():
    # Ideally, we will interrogate versioneer to find out the version of the
    # project we are building. Note that we can't simply look at
    # project.__version__ as we need the version string pre-build, so the
    # package may not be importable.
    for dir_ in dirs_containing_file('_version.py'):
        sys.path.insert(0, dir_)

        try:
            import _version
            yield _version.get_versions()['version']
        except Exception as e:
            print(e)
        finally:
            if '_version' in sys.modules:
                sys.modules.pop('_version')

            sys.path.pop(0)


def versions_from_miniver():
    def get_miniver_version(package_path):
        """Load version.py module without importing the whole package.

        Template code from miniver
        """
        import os
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location("version", os.path.join(package_path, "_version.py"))
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.__version__

    for dir_ in dirs_containing_file('_version.py'):
        try:
            yield get_miniver_version(dir_)
        except Exception as e:
            print(e)


def version_from_meta_yaml(path):
    from conda_build.metadata import MetaData
    return MetaData(path).version()


def get_version(path):
    # search for versioneer/miniver versions in our subdirs
    try:
        versions = list(versions_from_miniver())
    except:
        # Fall back to versioneer
        versions = list(versions_from_versioneer())

    if len(versions) == 1:
        version = versions[0]
        print('Found unambiguous version: {}'.format(version))
        if 'dirty' in version:
            print("WARNING - 'dirty' in version string - something has dirtied the working dir!")
            print("        - Printing git status/git diff to diagnose what's wrong")
            execute(['git', 'status'])
            execute(['git', 'diff'])
    elif len(versions) > 1:
        raise ValueError('Multiple _version.py files - cannot '
                         'resolve unambiguous version. '
                         'Versions found are: {}'.format(versions))
    else:
        # this project doesn't seem to be versioneer/miniver controlled - maybe the
        # version is hardcoded? Interrogate meta.yaml
        version = version_from_meta_yaml(path)

    if version is None:
        raise ValueError('Unable to detect version using conda, versioneer or miniver')

    return version


# booleans about the state of the the PEP440 tags.
is_tag = lambda v: '+' not in v
is_dev_tag = lambda v: v.split('.')[-1].startswith('dev')
is_rc_tag = lambda v: 'rc' in v.split('+')[0]
is_release_tag = lambda v: is_tag(v) and not (is_rc_tag(v) or is_dev_tag(v))


# -------------------------- BINSTAR INTEGRATION ---------------------------- #


class LetMeIn:
    def __init__(self, key):
        self.token = key
        self.site = False


def login_to_binstar():
    from binstar_client.utils import get_binstar
    return get_binstar()


def login_to_binstar_with_key(key):
    from binstar_client.utils import get_binstar
    return get_binstar(args=LetMeIn(key))


class BinstarFile(object):

    def __init__(self, full_name):
        self.full_name = full_name

    @property
    def user(self):
        return self.full_name.split('/')[0]

    @property
    def name(self):
        return self.full_name.split('/')[1]

    @property
    def basename(self):
        return '/'.join(self.full_name.split('/')[3:])

    @property
    def version(self):
        return self.full_name.split('/')[2]

    @property
    def platform(self):
        return self.full_name.replace('\\', '/').split('/')[3]

    @property
    def configuration(self):
        return self.full_name.replace('\\', '/').split('/')[4].split('-')[2].split('.')[0]

    def __str__(self):
        return self.full_name

    def __repr__(self):
        return self.full_name

    def all_info(self):
        s = ["         user: {}".format(self.user),
             "         name: {}".format(self.name),
             "     basename: {}".format(self.basename),
             "      version: {}".format(self.version),
             "     platform: {}".format(self.platform),
             "configuration: {}".format(self.configuration)]
        return "\n".join(s)


configuration_from_binstar_filename = lambda fn: fn.split('-')[-1].split('.')[0]
name_from_binstar_filename = lambda fn: fn.split('-')[0]
version_from_binstar_filename = lambda fn: fn.split('-')[1]
platform_from_binstar_filepath = lambda fp: p.split(p.split(fp)[0])[-1]


def binstar_channels_for_user(b, user):
    return b.list_channels(user).keys()


def binstar_files_on_channel(b, user, channel):
    info = b.show_channel(channel, user)
    return [BinstarFile(i['full_name']) for i in info['files']]


def binstar_remove_file(b, bfile):
    b.remove_dist(bfile.user, bfile.name, bfile.version, bfile.basename)


def files_to_remove(b, user, channel, filepath):
    platform_ = platform_from_binstar_filepath(filepath)
    filename = p.split(filepath)[-1]
    name = name_from_binstar_filename(filename)
    version = version_from_binstar_filename(filename)
    configuration = configuration_from_binstar_filename(filename)
    # find all the files on this channel
    all_files = binstar_files_on_channel(b, user, channel)
    # other versions of this exact setup that are not tagged versions should
    # be removed
    print('Removing old releases matching:'
          '\nname: {}\nconfiguration: {}\nplatform: {}'
          '\nversion: {}'.format(name, configuration, platform_, version))
    print('candidate releases with same name are:')
    pprint([f.all_info() for f in all_files if f.name == name])
    return [f for f in all_files if
            f.name == name and
            f.configuration == configuration and
            f.platform == platform_ and
            f.version != version and
            not is_release_tag(f.version) and
            same_version_different_build(version, f.version)]


def purge_old_binstar_files(b, user, channel, filepath):
    to_remove = files_to_remove(b, user, channel, filepath)
    print("Found {} releases to remove".format(len(to_remove)))
    for old_file in to_remove:
        print("Removing '{}'".format(old_file))
        binstar_remove_file(b, old_file)


def binstar_upload_unchecked(mc, key, user, channel, path):
    print('Uploading from {} using {}'.format(path, binstar(mc)))
    try:
        # TODO - could this safely be co? then we would get the binstar error..
        check([binstar(mc), '-t', key, 'upload',
               '--force', '-u', user, '-c', channel, path])
    except subprocess.CalledProcessError as e:
        # mask the binstar key...
        cmd = e.cmd
        cmd[2] = 'BINSTAR_KEY'
        # ...then raise the error
        raise subprocess.CalledProcessError(e.returncode, cmd)


def binstar_upload_if_appropriate(mc, path, user, key):
    if key is None:
        print('No binstar key provided')
    if user is None:
        print('No binstar user provided')
    if user is None or key is None:
        print('-> Unable to upload to binstar')
        return
    print('Have a user ({}) and key - can upload if suitable'.format(user))

    # decide if we should attempt an upload (if it's a PR we can't)
    if resolve_can_upload_from_ci():
        print('Auto resolving channel based on release type and CI status')
        channel = binstar_channel_from_ci(path)
        print("Fit to upload to channel '{}'".format(channel))
        binstar_upload_and_purge(mc, key, user, channel,
                                 get_conda_build_path(path))
    else:
        print("Cannot upload to binstar - must be a PR.")


def binstar_upload_and_purge(mc, key, user, channel, filepath):
    if not os.path.exists(filepath):
        raise ValueError('Built file {} does not exist. '
                         'UPLOAD FAILED.'.format(filepath))
    else:
        print('Uploading to {}/{}'.format(user, channel))
        binstar_upload_unchecked(mc, key, user, channel, filepath)
        b = login_to_binstar_with_key(key)
        if channel != 'main':
            print("Purging old releases from channel '{}'".format(channel))
            purge_old_binstar_files(b, user, channel, filepath)
        else:
            print("On main channel - no purging of releases will be done.")


# -------------- CONTINUOUS INTEGRATION-SPECIFIC FUNCTIONALITY -------------- #

is_on_appveyor = lambda: 'APPVEYOR' in os.environ
is_on_travis = lambda: 'TRAVIS' in os.environ
is_on_circleci = lambda: 'CIRCLECI' in os.environ

is_pr_from_travis = lambda: os.environ['TRAVIS_PULL_REQUEST'] != 'false'
is_pr_from_circlei = lambda: 'CIRCLE_PULL_REQUEST' in os.environ
is_pr_from_appveyor = lambda: 'APPVEYOR_PULL_REQUEST_NUMBER' in os.environ

branch_from_appveyor = lambda: os.environ['APPVEYOR_REPO_BRANCH']


def get_url(url):
    try:
        from urllib2 import urlopen, Request
    except ImportError:
        from urllib.request import urlopen, Request
    request = Request(url, headers={
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/74.0.3729.169 Safari/537.36"
    })
    return urlopen(request)


def get_default_branch_from_github(user, repo_name):
    import json

    url = "https://api.github.com/repos/{}/{}".format(user, repo_name)
    response = get_url(url)
    repo_data = json.loads(response.read().decode('utf-8'))
    return repo_data['default_branch']


def branch_from_travis():
    tag = os.environ['TRAVIS_TAG']
    branch = os.environ['TRAVIS_BRANCH']
    if tag == branch:
        print('WARNING - on travis and TRAVIS_TAG == TRAVIS_BRANCH. This '
              'suggests that we are building a tag.')
        repo_slug = os.environ['TRAVIS_REPO_SLUG']
        user, repo_name = repo_slug.split('/')
        branch = get_default_branch_from_github(user, repo_name)
        print('Detected a tag - building on default branch {}'.format(branch))

    assert branch
    return branch


def branch_from_circleci():
    branch = os.environ['CIRCLE_BRANCH']
    if not branch:
        # We are on a tag and we need to work out what the default
        assert os.environ['CIRCLE_TAG']
        branch = get_default_branch_from_github(os.environ['CIRCLE_PROJECT_USERNAME'],
                                                os.environ['CIRCLE_PROJECT_REPONAME'])
        print('Detected a tag - building on default branch {}'.format(branch))

    assert branch
    return branch


def circleci_build_is_tag():
    tag = os.environ.get('CIRCLE_TAG')
    return tag is not None


def travis_build_is_duplicate():
    tag = os.environ['TRAVIS_TAG']
    branch = os.environ['TRAVIS_BRANCH']
    # Travis will kick off two builds for tags - a build specifically for the tag, *and*
    # a normal build for the branch. This means we do the same work twice, which leads to
    # failures with uploads. Detect one of the two conditions here so we can bail.
    return branch == tag


def is_pr_on_ci():
    if is_on_travis():
        return is_pr_from_travis()
    elif is_on_appveyor():
        return is_pr_from_appveyor()
    elif is_on_circleci():
        return is_pr_from_circlei()
    else:
        raise ValueError("Not on appveyor, travis or CircleCI, so can't "
                         "resolve whether we are on a PR or not")


def branch_from_ci():
    if is_on_travis():
        return branch_from_travis()
    elif is_on_appveyor():
        return branch_from_appveyor()
    elif is_on_circleci():
        return branch_from_circleci()
    else:
        raise ValueError("We aren't on Appveyor, Travis or CircleCI so can't "
                         "decide on branch")


def resolve_can_upload_from_ci():
    # can upload as long as this isn't a PR
    can_upload = not is_pr_on_ci()
    print("Can we can upload (i.e. is this not a PR)? : {}".format(can_upload))
    return can_upload


def binstar_channel_from_ci(path):
    v = get_version(path)
    if is_release_tag(v):
        # tagged releases always go to main
        print("current head is a tagged release ({}), "
              "uploading to 'main' channel".format(v))
        return 'main'
    else:
        print('current head is not a release - interrogating CI to decide on '
              'channel to upload to (based on branch)')
        return branch_from_ci()


# -------------------- PYPI INTEGRATION ---------------------- #

PYPI_SDIST_UPLOAD_PYTHON_VERSION = os.environ.get('CONDACI_PYPI_SDIST_UPLOAD_PY_VER', '3.6')
pypirc_path = lambda: p.join(p.expanduser('~'), '.pypirc')
pypi_sdist_upload_allowed = lambda: (host_platform() == 'Linux' and
                                     PYTHON_VERSION ==
                                     PYPI_SDIST_UPLOAD_PYTHON_VERSION)

pypi_template = """[distutils]
index-servers =
    pypi
    pypitest

[pypi]
repository: https://upload.pypi.org/legacy/
username: {username}
password: {password}

[pypitest]
repository: https://test.pypi.org/legacy/
username: {test_username}
password: {test_password}"""


def pypi_setup_dotfile(username, password, test_username, test_password):
    path = pypirc_path()
    with open(path, 'wt') as f:
        f.write(pypi_template.format(username=username, password=password,
                                     test_username=test_username,
                                     test_password=test_password))
    return path


def upload_to_pypi_if_appropriate(mc, path, username, password,
                                  test_username, test_password):
    if username is None and test_username is None:
        print('No PyPI username provided')
        return
    if password is None and test_password is None:
        print('No PyPI password provided')
        return

    if not pypi_sdist_upload_allowed():
        print('Not on key node (Linux Python {}) - no PyPI sdist upload'
              .format(PYPI_SDIST_UPLOAD_PYTHON_VERSION))
        return

    if is_on_travis() and travis_build_is_duplicate():
        print('On travis and this is a duplicate build of a tag - '
              'travis will have also kicked off a build for the branch '
              'with this tag on it.')
        print('Not uploading to PyPi')
        return

    if is_on_circleci() and not circleci_build_is_tag():
        print('On CircleCI and this is not a tag build - not uploading to Pypi')
        return

    sdist_dir = p.join(get_dirty_work_dir(mc), 'dist/*')
    print('Found build sdist directory: {}'.format(sdist_dir))

    v = get_version(path)

    if is_rc_tag(v):
        print('RC tag: uploading to test PyPI repository')
        repo = 'pypitest'
    elif is_release_tag(v):
        print('Release tag: uploading to main PyPI repository')
        repo = 'pypi'
    else:
        print('Not release tag or RC tag - no PyPI upload')
        return

    print('Setting up .pypirc file..')
    twine_config_path = pypi_setup_dotfile(username, password,
                                           test_username, test_password)

    print("Uploading to PyPI user '{}'".format(username))
    execute([twine(mc), 'upload', '-r', repo,
             '--config-file', twine_config_path, sdist_dir])


# --------------------------- ARGPARSE COMMANDS ----------------------------- #

def miniconda_dir_cmd(_):
    set_globals_from_environ(verbose=False)
    print(miniconda_dir())


def setup_cmd(args):
    set_globals_from_environ()
    mc = miniconda_dir()
    setup_miniconda(mc, binstar_user=BINSTAR_USER, extra_channels=args.channels)


def build_cmd(args):
    set_globals_from_environ()
    mc = miniconda_dir()
    conda_meta = p.abspath(args.meta_yaml_dir)

    build_conda_package(mc, conda_meta, binstar_user=BINSTAR_USER)
    print('successfully built conda package, proceeding to upload')
    binstar_upload_if_appropriate(mc, conda_meta, BINSTAR_USER, BINSTAR_KEY)
    upload_to_pypi_if_appropriate(mc, conda_meta, PYPI_USER, PYPI_PASSWORD,
                                  PYPI_TEST_USER, PYPI_TEST_PASSWORD)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('usage: condaci.py [-h] {setup,build,miniconda_dir} ...')
        sys.exit(1)

    from argparse import ArgumentParser

    pa = ArgumentParser(
        description=r"""
        Sets up miniconda, builds, and uploads to Binstar.
        """)
    subp = pa.add_subparsers()

    sp = subp.add_parser('setup', help='Setup a miniconda environment')
    sp.add_argument('--channels', type=str, nargs='+',
                    help="A list of space separated conda channels to add "
                         "to the set of default channels. Note that they "
                         "are prepended to the default channels list in the "
                         "order provided and thus the first channel in the "
                         "list will have the highest precedence")
    sp.set_defaults(func=setup_cmd)

    bp = subp.add_parser('build', help='run a conda build')
    bp.add_argument('meta_yaml_dir',
                    help="Path to the dir containing the conda 'meta.yaml'"
                         "build script")
    bp.set_defaults(func=build_cmd)

    mp = subp.add_parser('miniconda_dir',
                         help='Path to the miniconda root directory')
    mp.set_defaults(func=miniconda_dir_cmd)

    args = pa.parse_args()
    args.func(args)

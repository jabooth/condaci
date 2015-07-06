#!/usr/bin/env python
import subprocess
import os
import os.path as p
from functools import partial
import platform as stdplatform
import uuid
import sys
from pprint import pprint

# on windows we have to download a small secondary script that configures
# Python 3 64-bit extensions. Here we define the URL and the local path that
# we will use for this script.
MAGIC_WIN_SCRIPT_URL = 'https://raw.githubusercontent.com/jabooth/python-appveyor-conda-example/master/continuous-integration/appveyor/run_with_env.cmd'
MAGIC_WIN_SCRIPT_PATH = r'C:\run_with_env.cmd'

# a random string we can use for the miniconda installer
# (to avoid name collisions)
RANDOM_UUID = uuid.uuid4()


# ------------------------------ UTILITIES ---------------------------------- #

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
    sentinal = ''
    if sys.version_info.major == 3:
        sentinal = b''
    for line in iter(proc.stdout.readline, sentinal):
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
    import urllib2
    f = urllib2.urlopen(url)
    with open(path_to_download, "wb") as fp:
        fp.write(f.read())
    fp.close()


def dirs_containing_file(fname, root=os.curdir):
    for path, dirs, files in os.walk(os.path.abspath(root)):
        if fname in files:
            yield path


def host_platform():
    return stdplatform.system()


def host_arch():
    arch = stdplatform.architecture()[0]
    # need to be a little more sneaky to check the platform on Windows:
    # http://stackoverflow.com/questions/2208828/detect-64bit-os-windows-in-python
    if host_platform() == 'Windows':
        if 'APPVEYOR' in os.environ:
            av_platform = os.environ['PLATFORM']
            if av_platform == 'x86':
                arch = '32bit'
            elif av_platform == 'x64':
                arch = '64bit'
            else:
                print('Was unable to interpret the platform "{}"'.format())
    return arch


# ------------------------ MINICONDA INTEGRATION ---------------------------- #

def url_for_platform_version(platform, py_version, arch):
    version = 'latest'
    base_url = 'http://repo.continuum.io/miniconda/Miniconda'
    platform_str = {'Linux': 'Linux',
                    'Darwin': 'MacOSX',
                    'Windows': 'Windows'}
    arch_str = {'64bit': 'x86_64',
                '32bit': 'x86'}
    ext = {'Linux': '.sh',
           'Darwin': '.sh',
           'Windows': '.exe'}

    if py_version == '3.4':
        base_url = base_url + '3'
    elif py_version != '2.7':
        raise ValueError("Python version must be '2.7 or '3.4'")
    return '-'.join([base_url, version,
                     platform_str[platform],
                     arch_str[arch]]) + ext[platform]


def temp_installer_path():
    # we need a place to download the miniconda installer too. use a random
    # string for the filename to avoid collisions, but choose the dir based
    # on platform
    return ('C:\{}.exe'.format(RANDOM_UUID) if host_platform() == 'Windows'
            else p.expanduser('~/{}.sh'.format(RANDOM_UUID)))


def default_miniconda_dir():
    # the directory where miniconda will be installed too
    return (p.expanduser('C:\Miniconda') if host_platform() == 'Windows'
            else p.expanduser('~/miniconda'))


# the script directory inside a miniconda install varies based on platform
def miniconda_script_dir_name():
    return 'Scripts' if host_platform() == 'Windows' else 'bin'


# handles to binaries from a miniconda install
miniconda_script_dir = lambda mc: p.join(mc, miniconda_script_dir_name())
conda = lambda mc: p.join(miniconda_script_dir(mc), 'conda')
binstar = lambda mc: p.join(miniconda_script_dir(mc), 'binstar')
python = lambda mc: p.join(miniconda_script_dir(mc), 'python')


def acquire_miniconda(url, path_to_download):
    print('Downloading miniconda from {} to {}'.format(url, path_to_download))
    download_file(url, path_to_download)


def install_miniconda(path_to_installer, path_to_install):
    print('Installing miniconda to {}'.format(path_to_install))
    if host_platform() == 'Windows':
        execute([path_to_installer, '/S', '/D={}'.format(path_to_install)])
    else:
        execute(['chmod', '+x', path_to_installer])
        execute([path_to_installer, '-b', '-p', path_to_install])


def setup_miniconda(python_version, installation_path, channel=None):
    url = url_for_platform_version(host_platform(), python_version,
                                   host_arch())
    print('Setting up miniconda from URL {}'.format(url))
    print("(Installing to '{}')".format(installation_path))
    acquire_miniconda(url, temp_installer_path())
    install_miniconda(temp_installer_path(), installation_path)
    # delete the installer now we are done
    os.unlink(temp_installer_path())
    conda_cmd = conda(installation_path)
    cmds = [[conda_cmd, 'update', '-q', '--yes', 'conda'],
            [conda_cmd, 'install', '-q', '--yes', 'conda-build', 'jinja2',
             'binstar']]
    if channel is not None:
        print("(adding channel '{}' for dependencies)".format(channel))
        cmds.append([conda_cmd, 'config', '--add', 'channels', channel])
    else:
        print("No channels have been configured (all dependencies have to be "
              "sourced from anaconda)")
    execute_sequence(*cmds)


# ------------------------ CONDA BUILD INTEGRATION -------------------------- #

def get_conda_build_path(path):
    from conda_build.metadata import MetaData
    from conda_build.build import bldpkg_path
    return bldpkg_path(MetaData(path))


def conda_build_package_win(mc, path):
    if 'BINSTAR_KEY' in os.environ:
        print('found BINSTAR_KEY in environment on Windows - deleting to '
              'stop vcvarsall from telling the world')
        del os.environ['BINSTAR_KEY']
    os.environ['PYTHON_ARCH'] = host_arch()[:2]
    os.environ['PYTHON_VERSION'] = '{}.{}'.format(sys.version_info.major,
                                                  sys.version_info.minor)
    print('PYTHON_ARCH={} PYTHON_VERSION={}'.format(os.environ['PYTHON_ARCH'],
                                                    os.environ['PYTHON_VERSION']))
    execute(['cmd', '/E:ON', '/V:ON', '/C', MAGIC_WIN_SCRIPT_PATH,
             conda(mc), 'build', '-q', path])


def build_conda_package(mc, path, channel=None):
    print('Building package at path {}'.format(path))
    print('Attempting to set CONDACI_VERSION environment variable')
    set_condaci_version()

    # this is a little menpo-specific, but we want to add the master channel
    # when doing dev builds to source our other dev dependencies
    v = get_version()
    if not (is_release_tag(v) or is_rc_tag(v)):
        print('building a non-release non-RC build - adding master channel.')
        if channel is None:
            print('warning - no channel provided - cannot add master channel')
        else:
            execute([conda(mc), 'config', '--add', 'channels', channel + '/channel/master'])
    else:
        print('building a RC or tag release - no master channel added.')

    if host_platform() == 'Windows':
        conda_build_package_win(mc, path)
    else:
        execute([conda(mc), 'build', '-q', path])


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


def version_from_git_tags():
    # if we can't use versioneer, we can manually fall back to look at git to
    # build our own PEP440 version
    raw = subprocess.check_output(['git', 'describe', '--tags']).strip()
    if sys.version_info.major == 3:
        # this always comes back as bytes. On Py3, convert to a string
        raw = raw.decode("utf-8")
    # git tags commonly start with a 'v' or 'V'
    if raw[0].lower() == 'v':
        raw = raw[1:]
    try:
        # raw of form 'VERSION-NCOMMITS-SHA - split it and rebuild in right way
        v, n_commits, sha = raw.split('-')
    except ValueError:
        # this version string is not as expected.
        print('warning - could not interpret version string from git - you '
              'may have a non-PEP440 version string')
        return raw
    else:
        return v + '+' + n_commits + '.' + sha


def get_version():
    # search for versioneer versions in our subdirs
    versions = list(versions_from_versioneer())

    if len(versions) == 1:
        version = versions[0]
        print('found single unambiguous versioneer version: {}'.format(version))
    else:
        print('WARNING: found no or multiple versioneer _version.py files - '
              'falling back to interrogate git manually for version')
        version = version_from_git_tags()
    return version

# booleans about the state of the the PEP440 tags.
is_tag = lambda v: '+' not in v
is_dev_tag = lambda v: v.split('.')[-1].startswith('dev')
is_rc_tag = lambda v: 'rc' in v.split('+')[0]
is_release_tag = lambda v: is_tag(v) and not (is_rc_tag(v) or is_dev_tag(v))


def set_condaci_version():
    # set the env variable CONDACI_VERSION to the current version (so it can
    # be used in meta.yaml pre-build)
    try:
        os.environ['CONDACI_VERSION'] = get_version()
    except subprocess.CalledProcessError:
        print('Warning - unable to set CONDACI_VERSION')


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


def binstar_upload_if_appropriate(mc, path, user, key, channel=None):
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
        if channel is None:
            print('No upload channel provided - auto resolving channel based '
                  'on release type and CI status')
            channel = binstar_channel_from_ci()
        print("Fit to upload to channel '{}'".format(channel))
        binstar_upload_and_purge(mc, key, user, channel,
                                 get_conda_build_path(path))
    else:
        print("Cannot upload to binstar - must be a PR.")


def binstar_upload_and_purge(mc, key, user, channel, filepath):
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

is_pr_from_travis = lambda: os.environ['TRAVIS_PULL_REQUEST'] != 'false'
is_pr_from_appveyor = lambda: 'APPVEYOR_PULL_REQUEST_NUMBER' in os.environ

branch_from_appveyor = lambda: os.environ['APPVEYOR_REPO_BRANCH']


def branch_from_travis():
    tag = os.environ['TRAVIS_TAG']
    branch = os.environ['TRAVIS_BRANCH']
    if tag == branch:
        print('WARNING - on travis and TRAVIS_TAG == TRAVIS_BRANCH. This '
              'suggests that we are building a tag.')
        print('Travis obscures the branch in this scenario, so we assume that'
              ' the branch is "master"')
        return 'master'
    else:
        return branch


def is_pr_on_ci():
    if is_on_travis():
        return is_pr_from_travis()
    elif is_on_appveyor():
        return is_pr_from_appveyor()
    else:
        raise ValueError("Not on appveyor or travis so can't "
                         "resolve whether we are on a PR or not")


def branch_from_ci():
    if is_on_travis():
        return branch_from_travis()
    elif is_on_appveyor():
        return branch_from_appveyor()
    else:
        raise ValueError("We aren't on "
                         "Appveyor or Travis so can't "
                         "decide on branch")


def resolve_can_upload_from_ci():
    # can upload as long as this isn't a PR
    can_upload = not is_pr_on_ci()
    print("Can we can upload? : {}".format(can_upload))
    return can_upload


def binstar_channel_from_ci():
    v = get_version()
    if is_release_tag(v):
        # tagged releases always go to main
        print("current head is a tagged release ({}), "
              "uploading to 'main' channel".format(get_version()))
        return 'main'
    else:
        print('current head is not a release - interrogating CI to decide on '
              'channel to upload to (based on branch)')
        return branch_from_ci()


# -------------------- [EXPERIMENTAL] PYPI INTEGRATION ---------------------- #

pypirc_path = p.join(p.expanduser('~'), '.pypirc')
pypi_upload_allowed = (host_platform() == 'Linux' and
                       host_arch() == '64bit' and
                       sys.version_info.major == 2)

pypi_template = """[distutils]
index-servers = pypi

[pypi]
username:{}
password:{}"""


def pypi_setup_dotfile(username, password):
    with open(pypirc_path, 'wb') as f:
        f.write(pypi_template.format(username, password))


def upload_to_pypi_if_appropriate(mc, username, password):
    if username is None or password is None:
        print('Missing PyPI username or password, skipping upload')
        return
    v = get_version()
    if not is_release_tag(v):
        print('Not on a tagged release - not uploading to PyPI')
        return
    if not pypi_upload_allowed:
        print('Not on key node (Linux 64 Py2) - no PyPI upload')
    print('Setting up .pypirc file..')
    pypi_setup_dotfile(username, password)
    print("Uploading to PyPI user '{}'".format(username))
    execute_sequence([python(mc), 'setup.py', 'sdist', 'upload'])


# --------------------------- ARGPARSE COMMANDS ----------------------------- #

def resolve_mc(mc):
    if mc is not None:
        return mc
    else:
        return default_miniconda_dir()


def setup_cmd(args):
    mc = resolve_mc(args.path)
    setup_miniconda(args.python, mc, channel=args.channel)
    if host_platform() == 'Windows':
        print('downloading magical Windows SDK configuration'
              ' script to {}'.format(MAGIC_WIN_SCRIPT_PATH))
        download_file(MAGIC_WIN_SCRIPT_URL, MAGIC_WIN_SCRIPT_PATH)


def build_cmd(args):
    mc = resolve_mc(args.miniconda)
    build_conda_package(mc, args.buildpath)


def binstar_cmd(args):
    mc = resolve_mc(args.miniconda)
    print('binstar being called with args: {}'.format(args))
    binstar_upload_if_appropriate(mc, args.buildpath, args.binstaruser,
                                  args.binstarkey, channel=args.binstarchannel)


def pypi_cmd(args):
    mc = resolve_mc(args.miniconda)
    upload_to_pypi_if_appropriate(mc, args.pypiuser, args.pypipassword)


def version_cmd(_):
    print(get_version())


def auto_cmd(args):
    mc = resolve_mc(args.miniconda)
    build_conda_package(mc, args.buildpath, channel=args.binstaruser)
    print('successfully built conda package, proceeding to upload')
    binstar_upload_if_appropriate(mc, args.buildpath, args.binstaruser,
                                  args.binstarkey,
                                  channel=args.binstarchannel)
    #upload_to_pypi_if_appropriate(mc, args.pypiuser, args.pypipassword)


def add_miniconda_parser(parser):
    parser.add_argument(
        "-m", "--miniconda",
        help="directory that miniconda is installed in (if not provided "
             "taken as '{}')".format(default_miniconda_dir()))


def add_pypi_parser(pa):
    pa.add_argument('--pypiuser',  nargs='?', default=None,
                    help='PyPI user to upload to')
    pa.add_argument('--pypipassword', nargs='?', default=None,
                    help='password of PyPI user')


def add_buildpath_parser(pa):
    pa.add_argument('buildpath',
                    help="path to the conda build scripts")


def add_binstar_parser(pa):
    pa.add_argument('--binstaruser', nargs='?', default=None,
                    help='Binstar user (or organisation) to upload to')
    pa.add_argument('--binstarchannel', nargs='?', default=None,
                    help='Binstar channel to uplaod to. If not provided will'
                         ' be calculated based on the environment')
    pa.add_argument('--binstarkey', nargs='?', default=None,
                    help='Binstar API key to use for uploading')


if __name__ == "__main__":
    from argparse import ArgumentParser
    pa = ArgumentParser(
        description=r"""
        Sets up miniconda, builds, and uploads to Binstar and PyPI.
        """)
    subp = pa.add_subparsers()

    sp = subp.add_parser('setup', help='setup a miniconda environment')
    sp.add_argument("python", choices=['2.7', '3.4'])
    sp.add_argument('-p', '--path', help='the path to install miniconda to. '
                                         'If not provided defaults to {'
                                         '}'.format(default_miniconda_dir()))
    sp.add_argument("-c", "--channel",
                    help="binstar channel to activate")
    sp.set_defaults(func=setup_cmd)

    bp = subp.add_parser('build', help='run a conda build')
    add_buildpath_parser(bp)
    add_miniconda_parser(bp)
    bp.set_defaults(func=build_cmd)

    bin = subp.add_parser('binstar', help='upload a conda build to binstar')
    add_buildpath_parser(bin)
    add_binstar_parser(bin)
    add_miniconda_parser(bin)
    bin.set_defaults(func=binstar_cmd)

    pypi = subp.add_parser('pypi', help='upload a source distribution to PyPI')
    add_pypi_parser(pypi)
    add_miniconda_parser(pypi)
    pypi.set_defaults(func=pypi_cmd)

    auto = subp.add_parser('auto', help='build and upload to binstar and pypi')
    add_buildpath_parser(auto)
    add_binstar_parser(auto)
    add_miniconda_parser(auto)
    add_pypi_parser(auto)
    auto.set_defaults(func=auto_cmd)

    vp = subp.add_parser('version', help='print the version as reported by '
                                         'versioneer or git')
    vp.set_defaults(func=version_cmd)

    args = pa.parse_args()
    args.func(args)
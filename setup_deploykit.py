#   Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# This file refered to github.com/onnx/onnx.git

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from distutils.spawn import find_executable
from distutils import sysconfig, log
import setuptools
import setuptools.command.build_py
import setuptools.command.develop
import setuptools.command.build_ext

from collections import namedtuple
from contextlib import contextmanager
import glob
import os
import shlex
import subprocess
import sys
import platform
from textwrap import dedent
import multiprocessing

setup_configs = dict()
setup_configs["BUILD_DEPLOYKIT"] = os.getenv("BUILD_DEPLOYKIT", "OFF")
# build with onnxruntime backend
setup_configs["ENABLE_ORT_BACKEND"] = os.getenv("ENABLE_ORT_BACKEND", "OFF")
setup_configs["ORT_DIRECTORY"] = os.getenv("ORT_DIRECTORY",
                                           os.path.dirname(__file__))
# build with tensorrt backend
setup_configs["ENABLE_TRT_BACKEND"] = os.getenv("ENABLE_TRT_BACKEND", "OFF")
setup_configs["TRT_DIRECTORY"] = os.getenv("TRT_DIRECTORY",
                                           os.path.dirname(__file__))
setup_configs["CUDA_DIRECTORY"] = os.getenv("CUDA_DIRECTORY",
                                            os.path.dirname(__file__))

setup_configs["BUILD_DEPLOYKIT_PYTHON"] = os.getenv("BUILD_DEPLOYKIT_PYTHON",
                                                    "ON")

TOP_DIR = os.path.realpath(os.path.dirname(__file__))
SRC_DIR = os.path.join(TOP_DIR, 'paddle2onnx')
CMAKE_BUILD_DIR = os.path.join(TOP_DIR, '.setuptools-cmake-build')

WINDOWS = (os.name == 'nt')

CMAKE = find_executable('cmake3') or find_executable('cmake')
MAKE = find_executable('make')

setup_requires = []
extras_require = {}

################################################################################
# Global variables for controlling the build variant
################################################################################

# Default value is set to TRUE\1 to keep the settings same as the current ones.
# However going forward the recomemded way to is to set this to False\0
USE_MSVC_STATIC_RUNTIME = bool(os.getenv('USE_MSVC_STATIC_RUNTIME', '1') == '1')
ONNX_NAMESPACE = os.getenv('ONNX_NAMESPACE', 'paddle2onnx')
################################################################################
# Version
################################################################################

try:
    git_version = subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=TOP_DIR).decode('ascii').strip()
except (OSError, subprocess.CalledProcessError):
    git_version = None

with open(os.path.join(TOP_DIR, 'VERSION_NUMBER')) as version_file:
    VersionInfo = namedtuple('VersionInfo', ['version', 'git_version'])(
        version=version_file.read().strip(), git_version=git_version)

################################################################################
# Pre Check
################################################################################

assert CMAKE, 'Could not find "cmake" executable!'

################################################################################
# Utilities
################################################################################


@contextmanager
def cd(path):
    if not os.path.isabs(path):
        raise RuntimeError('Can only cd to absolute path, got: {}'.format(path))
    orig_path = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(orig_path)


################################################################################
# Customized commands
################################################################################


class ONNXCommand(setuptools.Command):
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass


class create_version(ONNXCommand):
    def run(self):
        with open(os.path.join(SRC_DIR, 'version.py'), 'w') as f:
            f.write(
                dedent('''\
            # This file is generated by setup.py. DO NOT EDIT!
            from __future__ import absolute_import
            from __future__ import division
            from __future__ import print_function
            from __future__ import unicode_literals
            version = '{version}'
            git_version = '{git_version}'
            '''.format(**dict(VersionInfo._asdict()))))


class cmake_build(setuptools.Command):
    """
    Compiles everything when `python setupmnm.py build` is run using cmake.
    Custom args can be passed to cmake by specifying the `CMAKE_ARGS`
    environment variable.
    The number of CPUs used by `make` can be specified by passing `-j<ncpus>`
    to `setup.py build`.  By default all CPUs are used.
    """
    user_options = [(str('jobs='), str('j'),
                     str('Specifies the number of jobs to use with make'))]

    built = False

    def initialize_options(self):
        self.jobs = None

    def finalize_options(self):
        if sys.version_info[0] >= 3:
            self.set_undefined_options('build', ('parallel', 'jobs'))
        if self.jobs is None and os.getenv("MAX_JOBS") is not None:
            self.jobs = os.getenv("MAX_JOBS")
        self.jobs = multiprocessing.cpu_count() if self.jobs is None else int(
            self.jobs)

    def run(self):
        if cmake_build.built:
            return
        cmake_build.built = True
        if not os.path.exists(CMAKE_BUILD_DIR):
            os.makedirs(CMAKE_BUILD_DIR)

        with cd(CMAKE_BUILD_DIR):
            build_type = 'Release'
            # configure
            cmake_args = [
                CMAKE,
                '-DPYTHON_INCLUDE_DIR={}'.format(sysconfig.get_python_inc()),
                '-DPYTHON_EXECUTABLE={}'.format(sys.executable),
                '-DBUILD_PADDLE2ONNX_PYTHON=ON',
                '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON',
                '-DONNX_NAMESPACE={}'.format(ONNX_NAMESPACE),
                '-DPY_EXT_SUFFIX={}'.format(
                    sysconfig.get_config_var('EXT_SUFFIX') or ''),
            ]
            cmake_args.append('-DCMAKE_BUILD_TYPE=%s' % build_type)
            for k, v in setup_configs.items():
                cmake_args.append("-D{}={}".format(k, v))
            if WINDOWS:
                cmake_args.extend([
                    # we need to link with libpython on windows, so
                    # passing python version to window in order to
                    # find python in cmake
                    '-DPY_VERSION={}'.format('{0}.{1}'.format(* \
                                                              sys.version_info[:2])),
                ])
                if platform.architecture()[0] == '64bit':
                    cmake_args.extend(['-A', 'x64', '-T', 'host=x64'])
                else:
                    cmake_args.extend(['-A', 'Win32', '-T', 'host=x86'])
            if 'CMAKE_ARGS' in os.environ:
                extra_cmake_args = shlex.split(os.environ['CMAKE_ARGS'])
                # prevent crossfire with downstream scripts
                del os.environ['CMAKE_ARGS']
                log.info('Extra cmake args: {}'.format(extra_cmake_args))
                cmake_args.extend(extra_cmake_args)
            cmake_args.append(TOP_DIR)
            subprocess.check_call(cmake_args)

            build_args = [CMAKE, '--build', os.curdir]
            if WINDOWS:
                build_args.extend(['--config', build_type])
                build_args.extend(['--', '/maxcpucount:{}'.format(self.jobs)])
            else:
                build_args.extend(['--', '-j', str(self.jobs)])
            subprocess.check_call(build_args)


class build_py(setuptools.command.build_py.build_py):
    def run(self):
        self.run_command('create_version')
        self.run_command('cmake_build')

        generated_python_files = \
            glob.glob(os.path.join(CMAKE_BUILD_DIR, 'paddle2onnx', '*.py')) + \
            glob.glob(os.path.join(CMAKE_BUILD_DIR, 'paddle2onnx', '*.pyi'))

        for src in generated_python_files:
            dst = os.path.join(TOP_DIR, os.path.relpath(src, CMAKE_BUILD_DIR))
            self.copy_file(src, dst)

        return setuptools.command.build_py.build_py.run(self)


class develop(setuptools.command.develop.develop):
    def run(self):
        self.run_command('build_py')
        setuptools.command.develop.develop.run(self)


class build_ext(setuptools.command.build_ext.build_ext):
    def run(self):
        self.run_command('cmake_build')
        setuptools.command.build_ext.build_ext.run(self)

    def build_extensions(self):
        for ext in self.extensions:
            fullname = self.get_ext_fullname(ext.name)
            filename = os.path.basename(self.get_ext_filename(fullname))

            lib_path = CMAKE_BUILD_DIR
            if os.name == 'nt':
                debug_lib_dir = os.path.join(lib_path, "Debug")
                release_lib_dir = os.path.join(lib_path, "Release")
                if os.path.exists(debug_lib_dir):
                    lib_path = debug_lib_dir
                elif os.path.exists(release_lib_dir):
                    lib_path = release_lib_dir
            src = os.path.join(lib_path, filename)
            dst = os.path.join(
                os.path.realpath(self.build_lib), "paddle2onnx", filename)
            self.copy_file(src, dst)


class mypy_type_check(ONNXCommand):
    description = 'Run MyPy type checker'

    def run(self):
        """Run command."""
        onnx_script = os.path.realpath(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "tools/mypy-onnx.py"))
        returncode = subprocess.call([sys.executable, onnx_script])
        sys.exit(returncode)


cmdclass = {
    'create_version': create_version,
    'cmake_build': cmake_build,
    'build_py': build_py,
    'develop': develop,
    'build_ext': build_ext,
    'typecheck': mypy_type_check,
}

################################################################################
# Extensions
################################################################################

ext_modules = [
    setuptools.Extension(
        name=str('paddle2onnx.paddle2onnx_cpp2py_export'), sources=[]),
    setuptools.Extension(
        name=str('paddle2onnx.deploykit_cpp2py_export'), sources=[]),
]

################################################################################
# Packages
################################################################################

# no need to do fancy stuff so far
packages = setuptools.find_packages()

################################################################################
# Test
################################################################################

setup_requires.append('pytest-runner')

if sys.version_info[0] == 3:
    # Mypy doesn't work with Python 2
    extras_require['mypy'] = ['mypy==0.600']

################################################################################
# Final
################################################################################

setuptools.setup(
    name="paddle2onnx",
    version=VersionInfo.version,
    description="Export PaddlePaddle to ONNX",
    ext_modules=ext_modules,
    cmdclass=cmdclass,
    packages=packages,
    include_package_data=True,
    setup_requires=setup_requires,
    extras_require=extras_require,
    author='paddle-infer',
    author_email='paddle-infer@baidu.com',
    url='https://github.com/PaddlePaddle/Paddle2ONNX.git',
    install_requires=['six', 'protobuf', 'onnx<=1.9.0'],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    license='Apache 2.0',
    entry_points={'console_scripts': ['paddle2onnx=paddle2onnx.command:main']})
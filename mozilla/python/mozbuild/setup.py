# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

try:
    from setuptools import setup,find_packages
except:
    from distutils.core import setup,find_packages

VERSION = '0.1'

setup(
    name='mozbuild',
    description='Mozilla build system functionality.',
    license='MPL 2.0',
    packages=find_packages(),
    version=VERSION
)

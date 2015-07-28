# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
##
# The Initial Developer of the Original Code is
# Yonggang Luo.
# Contributor(s):
#  Yonggang Luo <luoyonggang@gmail.com>
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****

import os
import sys
import json
import subprocess

from setuptools import setup, find_packages
from setuptools.command.install import install as base_install

desc = """Full mozbuild package."""
summ = """A full mozbuild toolchain split from gecko source tree."""

PACKAGE_NAME = "mozbuildfull"
PACKAGE_VERSION = "0.1.0"

class DecircularJSONEncoder(json.JSONEncoder):
  hash = set()
  def default(self, o):
      if o in self.hash:
        return None
      self.hash.add(o)
      if (hasattr(o, '__dict__')):
        return o.__dict__    
      return None

class install(base_install):
    def run(self):
        base_install.run(self)
        self.post_install()

    def call_setup(self, directory, arguments):
        """Calls setup.py in a directory."""
        setup = os.path.join(directory, 'setup.py')

        program = [sys.executable, setup]
        program.extend(arguments)

        # We probably could call the contents of this file inside the context
        # of this interpreter using execfile() or similar. However, if global
        # variables like sys.path are adjusted, this could cause all kinds of
        # havoc. While this may work, invoking a new process is safer.

        try:
            output = subprocess.check_output(program, cwd=directory, stderr=subprocess.STDOUT)
            print(output)
        except subprocess.CalledProcessError as e:
            if 'Python.h: No such file or directory' in e.output:
                print('WARNING: Python.h not found. Install Python development headers.')
            else:
                print(e.output)

            raise Exception('Error installing package: %s' % directory)

    def post_install(self):
        command_obj = self.distribution.command_obj
        targetPath = None
        if 'install_egg_info' in command_obj:
          egg_info = command_obj['install_egg_info']
          #print(DecircularJSONEncoder(check_circular=False, indent=2).encode(egg_info))
          targetPath = egg_info.target
        else:
          targetPath = self.install_lib
        packages = []
        with open(os.path.join(self.install_lib, 'mozbuild', 'mozbuild_package.pth'), 'r') as f:
          for package in f.readlines():
            package = 'mozbuild' + '/' + package.strip()
            if os.path.exists(os.path.join(self.install_lib, package)):
              packages.append(package)
        with open(os.path.join(self.install_lib, 'mozbuild', 'mozbuild_package.json'), 'r') as f:
          data = json.loads(f.read())
          for package, arguments in data.items():
              packagePath = os.path.join(self.install_lib, 'mozbuild', package)
              if not os.path.exists(packagePath):
                continue
              try:
                  self.call_setup(packagePath, arguments)
              except Exception as e:
                print(e)

        with open(os.path.join(self.install_lib, 'mozbuild_package.pth'), 'w') as f:
          for package in packages:
            f.write('%s\n' % package)

setup(name=PACKAGE_NAME,
      version=PACKAGE_VERSION,
      description=desc,
      long_description=summ,
      author='Yonggang Luo, Kingsoft',
      author_email='luoyonggang@gmail.com',
      url='http://github.com/html-shell/mozbuild',
      license='http://www.mozilla.org/MPL/',
      include_package_data=True,
      packages=['mozbuild'],
      zip_safe=False,
      entry_points='',
      platforms =['Any'],
      install_requires = [],
      cmdclass={'install': install},
      classifiers=['Development Status :: 4 - Beta',
                   'Environment :: Console',
                   'Intended Audience :: Developers',
                   'License :: OSI Approved :: Apache Software License',
                   'Operating System :: MacOS :: MacOS X',
                   'Operating System :: Microsoft :: Windows :: Windows NT/2000',
                   'Operating System :: Microsoft',
                   'Operating System :: OS Independent',
                   'Operating System :: POSIX :: BSD :: FreeBSD',
                   'Operating System :: POSIX :: Linux',
                   'Operating System :: POSIX :: SunOS/Solaris',
                   'Operating System :: POSIX',
                   'Topic :: Software Development :: Libraries :: Python Modules',
                  ]
     )

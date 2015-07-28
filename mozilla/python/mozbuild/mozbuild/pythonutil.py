# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import sys
import mozpack.path as mozpath

def iter_modules_in_path(*paths):
    normal_paths = [os.path.abspath(os.path.normcase(p)) + os.sep
             for p in paths]
    for name, module in sys.modules.items():
        if not hasattr(module, '__file__'):
            continue

        path = module.__file__

        if path.endswith('.pyc'):
            path = path[:-1]
        normal_path = os.path.abspath(os.path.normcase(path))

        if any(normal_path.startswith(p) for p in normal_paths):
            yield mozpath.abspath(path)

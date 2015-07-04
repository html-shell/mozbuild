from __future__ import unicode_literals

import errno
import os
import re
import types
import uuid

from xml.dom import getDOMImplementation

from mozpack.files import FileFinder
from reftest import ReftestManifest

from mozpack.copier import FilePurger
from mozpack.manifests import (
    InstallManifest,
)
import mozpack.path as mozpath

from .common import CommonBackend
from .visualstudio import VisualStudioBackend
from ..frontend.data import (
    GeneratedInclude,
    Exports,
    JsPreferenceFile,
    ConfigFileSubstitution,
    TestManifest,
    VariablePassthru,
    TestHarnessFiles,
    JARManifest,
    GeneratedFile,
    ReaderSummary,
    DirectoryTraversal,
    FinalTargetFiles,
    Program,
    SimpleProgram,
    JavaScriptModules,
    XPIDLFile
)

class InternalBackend(VisualStudioBackend):
    def _init(self):
        CommonBackend._init(self)
        self._paths_to_sources = {}
        self._path_to_unified_sources = set();
        self._paths_to_includes = {}
        self._paths_to_defines = {}
        self._paths_to_configs = {}
        self._libs_to_paths = {}

        def detailed(summary):
            return 'Building with internal backend'
        self.summary.backend_detailed_summary = types.MethodType(detailed,
            self.summary)
        self.typeSet = set()

    def consume_object(self, obj):
        # Just acknowledge everything.
        handled = VisualStudioBackend.consume_object(self, obj)
        if handled:
            return
        if isinstance(obj, Exports):
            self._process_exports(obj, obj.exports)

    def _walk_hierarchy(self, obj, element, namespace=''):
        """Walks the ``HierarchicalStringList`` ``element`` in the context of
        the mozbuild object ``obj`` as though by ``element.walk()``, but yield
        three-tuple containing the following:

        - ``source`` - The path to the source file named by the current string
        - ``dest``   - The relative path, including the namespace, of the
                       destination file.
        - ``flags``  - A dictionary of flags associated with the current string,
                       or None if there is no such dictionary.
        """
        for path, strings in element.walk():
            for s in strings:
                source = mozpath.normpath(mozpath.join(obj.srcdir, s))
                dest = mozpath.join(namespace, path, mozpath.basename(s))
                yield source, dest, strings.flags_for(s)

    def _process_exports(self, obj, exports):
        for source, dest, y in self._walk_hierarchy(obj, exports):
            print(source, dest, y)

    def consume_finished(self):
        print(self.typeSet)
        pass

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

from .common import (
    CommonBackend,
    XPIDLManager,
    TestManager,
    WebIDLCollection
)

from .visualstudio import VisualStudioBackend
from ..frontend.data import (
    ContextDerived,
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
    XPIDLFile,
    UnifiedSources,
    HostSimpleProgram,
    HostProgram,
    HostLibrary,
)

def _get_attribute_with(v, k, default = {}):
    if not k in v:
        v[k] = default
    return v[k]

class InternalBackend(VisualStudioBackend):
    def _init(self):

        self._idl_manager = XPIDLManager(self.environment)
        self._test_manager = TestManager(self.environment)
        self._webidls = WebIDLCollection()
        self._configs = set()
        self._ipdl_sources = set()
        self._paths_to_sources = {}
        self._path_to_unified_sources = set();
        self._paths_to_includes = {}
        self._paths_to_defines = {}
        self._paths_to_configs = {}
        self._libs_to_paths = {}
        self._paths_to_export = {}
        self._extra_components = set()
        self._extra_pp_components = set()
        self._extra_pp_modules = set()
        self._js_preference_files = set()
        self._jar_manifests = set()
        self._cc_configs = {}
        self._python_unit_tests = set()
        self._garbages = set()
        self._install_manifests = {
            k: InstallManifest() for k in [
                'dist_bin',
                'dist_idl',
                'dist_include',
                'dist_public',
                'dist_private',
                'dist_sdk',
                'dist_xpi-stage',
                'tests',
                'xpidl',
            ]}

        def detailed(summary):
            return 'Building with internal backend finished.'
        self.summary.backend_detailed_summary = types.MethodType(detailed,
            self.summary)
        self.typeSet = set()

    def consume_object(self, obj):
        handled = False
        if isinstance(obj, ContextDerived):
            handled = CommonBackend.consume_object(self, obj)
        if handled and not isinstance(obj, UnifiedSources): #UnifiedSources should handled by visual studio
            return
        handled = VisualStudioBackend.consume_object(self, obj)
        if handled:
            return
        if isinstance(obj, DirectoryTraversal):
            #No need to handle
            pass
        elif isinstance(obj, Exports):
            self._process_exports(obj, obj.exports)
        elif isinstance(obj, GeneratedFile):
            #TODO: no handle this time
            pass
        elif isinstance(obj, VariablePassthru):
            self._process_variable_passthru(obj)
        elif isinstance(obj, JsPreferenceFile):
            self._js_preference_files.add(self._get_full_path(obj, obj.path))
        elif isinstance(obj, ConfigFileSubstitution):
            #TODO: no handle this time
            pass
        elif isinstance(obj, JARManifest):
            self._jar_manifests.add
        elif isinstance(obj, TestHarnessFiles):
            self._process_test_harness_files(obj)
        elif isinstance(obj, ReaderSummary):
            #No need to handle
            pass
        elif isinstance(obj, HostSimpleProgram):
            #TODO: no handle this time
            pass
        elif isinstance(obj, HostProgram):
            #TODO: no handle this time
            pass
        elif isinstance(obj, Program):
            #TODO: no handle this time
            pass
        elif isinstance(obj, SimpleProgram):
            #TODO: no handle this time
            pass
        elif isinstance(obj, HostLibrary):
            #TODO: no handle this time
            pass
        elif isinstance(obj, FinalTargetFiles):
            self._process_final_target_files(obj, obj.files, obj.target)
        elif isinstance(obj, JavaScriptModules):
            self._process_javascript_modules(obj)
        elif isinstance(obj, GeneratedInclude):
            self._process_generated_include(obj)
        else:
            self.typeSet.add(obj.__class__.__name__)

    def _process_generated_include(self, obj):
        if obj.path.startswith('/'):
            path = self.environment.topobjdir.replace('\\', '/') + obj.path
        else:
            path = os.path.join(obj.srcdir, obj.path)
        path = os.path.normpath(path).replace('\\', '/')
        srcdirConfig = _get_attribute_with(self._cc_configs, obj.srcdir)
        _get_attribute_with(srcdirConfig, 'GENERATED_INCLUDES', []).append(path)

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
        for source, dest, _ in self._walk_hierarchy(obj, exports):
            self._install_manifests['dist_include'].add_symlink(source, dest)

            if not os.path.exists(source):
                raise Exception('File listed in EXPORTS does not exist: %s' % source)

    def _process_javascript_modules(self, obj):
        if obj.flavor == 'extra':
            self._process_final_target_files(obj, obj.modules, 'dist/bin/modules')
            return

        if obj.flavor == 'extra_pp':
            for path, strings in obj.modules.walk():
                if not strings:
                    continue
                #print(path, list(strings))
            return

        if obj.flavor == 'testing':
            manifest = self._install_manifests['tests']
            for source, dest, _ in self._walk_hierarchy(obj, obj.modules):
                manifest.add_symlink(source, mozpath.join('modules', dest))
            return

        raise Exception('Unsupported JavaScriptModules instance: %s' % obj.flavor)

    def _process_final_target_files(self, obj, files, target):
        if target.startswith('dist/bin'):
            install_manifest = self._install_manifests['dist_bin']
            reltarget = mozpath.relpath(target, 'dist/bin')
        elif target.startswith('dist/xpi-stage'):
            install_manifest = self._install_manifests['dist_xpi-stage']
            reltarget = mozpath.relpath(target, 'dist/xpi-stage')
        else:
            raise Exception("Cannot install to " + target)

        for path, strings in files.walk():
            for f in strings:
                source = mozpath.normpath(os.path.join(obj.srcdir, f))
                dest = mozpath.join(reltarget, path, mozpath.basename(f))
                install_manifest.add_symlink(source, dest)

    def _process_test_harness_files(self, obj):
        for path, files in obj.srcdir_files.iteritems():
            for source in files:
                dest = '%s/%s' % (path, mozpath.basename(source))
                self._install_manifests['tests'].add_symlink(source, dest)

        for path, patterns in obj.srcdir_pattern_files.iteritems():
            for p in patterns:
                self._install_manifests['tests'].add_pattern_symlink(obj.srcdir, p, path)

        for path, files in obj.objdir_files.iteritems():
            #TODO: no handle this time
            pass

    def _get_full_path(self, obj, filename):
        return os.path.normpath(os.path.join(obj.srcdir, filename))

    def _process_variable_passthru(self, obj):
        cc_flags = [
            'DISABLE_STL_WRAPPING',
            'VISIBILITY_FLAGS',
            'RCINCLUDE',
            'MSVC_ENABLE_PGO',
            'DEFFILE',
            'USE_STATIC_LIBS',
            'MOZBUILD_CXXFLAGS',
            'MOZBUILD_CFLAGS',
            'NO_PROFILE_GUIDED_OPTIMIZE',
            'WIN32_EXE_LDFLAGS',
            'MOZBUILD_LDFLAGS',
            'FAIL_ON_WARNINGS',
            'EXTRA_COMPILE_FLAGS',
            'RCFILE',
            'RESFILE',
            'NO_DIST_INSTALL',
            'IS_GYP_DIR',
        ]

        # Sorted so output is consistent and we don't bump mtimes.
        for k, v in sorted(obj.variables.items()):
            if k == 'EXTRA_COMPONENTS':
                for f in v:
                    self._extra_components.add(self._get_full_path(obj, f))
            elif k == 'EXTRA_PP_COMPONENTS':
                for f in v:
                    self._extra_pp_components.add(self._get_full_path(obj, f))
            elif k == 'PYTHON_UNIT_TESTS':
                for p in v:
                    self._python_unit_tests.add(self._get_full_path(obj, p))
            elif k == 'GARBAGE':
                for p in v:
                    self._garbages.add(self._get_full_path(obj, p))
            elif k in cc_flags:
                _get_attribute_with(self._cc_configs, obj.srcdir)[k] = v
            else:
                print(k, v)

    def print_list(self, v):
        for x in sorted(list(v)):
            print(x)

    def consume_finished(self):
        CommonBackend.consume_finished(self)
        print(self.typeSet)
        #self.print_list(self._garbages)
        #self.print_list(self._python_unit_tests)
        #self.print_list(self.backend_input_files) # moz.build files
        #print(self._cc_configs)
        #self.print_list(self._extra_pp_components)
        #self.print_list(self._js_preference_files)

    def _handle_idl_manager(self, manager):#For CommonBackend to call
        build_files = self._install_manifests['xpidl']

        for idl in manager.idls.values():
            self._install_manifests['dist_idl'].add_symlink(idl['source'],
                idl['basename'])
            self._install_manifests['dist_include'].add_optional_exists('%s.h'
                % idl['root'])

        for module in manager.modules:
            build_files.add_optional_exists(mozpath.join('.deps',
                '%s.pp' % module))

    def _handle_ipdl_sources(self, ipdl_dir,
        sorted_ipdl_sources, unified_ipdl_cppsrcs_mapping
    ):
        #TODO: not implemented yet
        pass

    def _handle_webidl_build(self, bindings_dir, unified_source_mapping,
                             webidls, expected_build_output_files,
                             global_define_files):
        include_dir = mozpath.join(self.environment.topobjdir, 'dist',
            'include')
        for f in expected_build_output_files:
            if f.startswith(include_dir):
                self._install_manifests['dist_include'].add_optional_exists(
                    mozpath.relpath(f, include_dir))

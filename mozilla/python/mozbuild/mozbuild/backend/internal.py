from __future__ import unicode_literals

import errno
import os
import sys
import re
import time
import types
import uuid
import copy
import io
import Queue as queue

from xml.dom import getDOMImplementation

from mozpack.files import FileFinder
from reftest import ReftestManifest
from mozbuild.action import buildlist
from mozbuild.action.process_install_manifest import process_manifest
from mozpack.copier import (
    FilePurger,
)
from mozpack.manifests import (
    InstallManifest,
)
from mozbuild import jar
import mozpack.path as mozpath

from .base import (
    BuildBackend
)

from .common import (
    CommonBackend,
    XPIDLManager,
    TestManager,
    WebIDLCollection
)

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
    InstallationTarget,
    Defines,
    GeneratedSources,
    HostSources,
    Library,
    SharedLibrary,
    LocalInclude,
    Sources,
    UnifiedSources,
)

from ..util import (
    ensureParentDir,
    ReadOnlyDict,
)

def get_define(defines, k):
    if k == 'RELATIVESRCDIR':
        return None
    if defines[k] is None:
        return None
    if defines[k] is False:
        return ''
    if defines[k] is True:
        return '1'
    return defines[k]

def get_define_list(defines):
    return [(name, get_define(defines, name)) for name in defines if (get_define(defines, name) is not None)]

def compute_defines_from_dict(new_defines,  define_type = 'dict', prefix='-D'):
    l = get_define_list(new_defines)
    if define_type == 'list':
        return l
    if define_type == 'DEFINES':
        return [prefix + '%s%s' % (name, ('' if not value else '='+ str(value))) for (name, value) in l]

    if define_type == 'ACDEFINES':
        return ' '.join([prefix + '%s=%s' % (name,
            shell_quote(new_defines[name]).replace('$', '$$')) for name in new_defines if new_defines[name]])
    if define_type == 'ALLDEFINES':
        return '\n'.join(sorted(['#define %s %s' % (name,
            new_defines[name]) for name in new_defines]))

#define_type could be ACDEFINES ALLDEFINES or dict or list
def compute_defines(config, define_type = 'dict', defines=None, prefix='-D'):
    new_defines = dict(config.defines)
    for x in config.non_global_defines:
        if x in new_defines:
            del new_defines[x]
    if defines != None:
        new_defines.update(defines)
    if define_type == 'dict':
        return new_defines
    return compute_defines_from_dict(new_defines, define_type, prefix)

def get_slots(t):
    slots = [];
    for cls in t.__mro__:
        __slots = getattr(cls, '__slots__', None)
        if __slots is None:
            continue
        if isinstance(__slots, types.UnicodeType) \
            or isinstance(__slots, types.StringType):
            slots.append(__slots)
            continue
        slots += __slots
    return slots

class InternalBackend(CommonBackend):
    def __init__(self, environment):
        self.depsJson = mozpath.join(environment.topobjdir, 'deps.json')
        self.manifests_root = mozpath.join(environment.topobjdir, '_build_manifests/install')
        #TODO: Remove the manifest files
        self.dep_path = mozpath.join(environment.topobjdir, '_build_manifests', '.deps', 'install')
        self._compute_xul_flags(environment)

        config = environment
        if 'LIBXUL_SDK' in config.substs:
            self.libxul_sdk = config.substs['LIBXUL_SDK']
            self.IDL_PARSER_CACHE_DIR = mozpath.join(config.substs['LIBXUL_SDK'], 'sdk/bin')
        else:
            self.libxul_sdk = mozpath.join(config.topobjdir, 'dist')
            self.IDL_PARSER_CACHE_DIR = mozpath.join(config.topobjdir, 'dist/sdk/bin')

        self.init_all_configs = {
            'topdirs': {},
            'srcdirs': {},
            'garbages': set(),
            'python_unit_tests': set(),

            'paths_to_unifies': {},
            'paths_to_sources': {},
            'paths_components_files': {},
            'path_to_unified_sources': set(),
            'paths_to_includes': {},
            'paths_to_defines': {},
            'libs_to_paths': {},
            'backend_input_files': set(),
            'backend_output_files': set(),
            'test_manifests': {},
            'xpt_list': [],
            'libs_link_into': {},
            'top_libs': {},
            'chrome_files': set(),
            'idl_set': set(),
        }

        self._paths_to_configs = {}

        self._install_manifests = {
            k: InstallManifest() for k in [
                'dist',
                'tests',
                'build',
            ]
        }
        '''
        self._install_manifests = {
            k: InstallManifest() for k in [
                'dist_bin',
                'dist_idl',
                'dist_include',
                'dist_public',
                'dist_private',
                'dist_sdk',
                'dist_xpi-stage',
                'dist_branding',
                'tests',
                #'xpidl', This is not neeeded in internal build
            ]}
        '''

        self.typeSet = set()

        BuildBackend.__init__(self, environment)

    def _init_with(self, all_configs):
        self.all_configs = all_configs
        self.backend_input_files = all_configs['backend_input_files']
        self._backend_output_files = all_configs['backend_output_files']

        self._topdirs_config = all_configs['topdirs']
        self._garbages = all_configs['garbages']
        self._python_unit_tests = all_configs['python_unit_tests']

        self._paths_to_unifies = all_configs['paths_to_unifies']
        self._paths_to_sources = all_configs['paths_to_sources']
        self._paths_components_files = all_configs['paths_components_files']
        self._path_to_unified_sources  = all_configs['path_to_unified_sources']
        self._paths_to_includes = all_configs['paths_to_includes']
        self._paths_to_defines = all_configs['paths_to_defines']
        self._libs_to_paths = all_configs['libs_to_paths']
        self._libs_link_into = all_configs['libs_link_into']
        self._top_libs = all_configs['top_libs']
        self._test_manifests = all_configs['test_manifests']
        self._chrome_set = all_configs['chrome_files']
        self._xpt_list = all_configs['xpt_list']
        self._idl_set = all_configs['idl_set']

    def _add_jar_install_list(self, obj, installList, preprocessor = False):
        for s,d in installList:
            target = mozpath.relpath(d, obj.topobjdir)
            self._process_files(obj, [s], target, preprocessor = preprocessor, marker='jar', target_is_file = True)

    def _get_config(self, srcdir):
        return self.all_configs['srcdirs'].setdefault(srcdir, {})

    def _compute_xul_flags(self, config):
        substs = config.substs
        XULPPFLAGS = substs['MOZ_DEBUG_ENABLE_DEFS'] if substs['MOZ_DEBUG'] else substs['MOZ_DEBUG_DISABLE_DEFS']
        self.XULPPFLAGS = XULPPFLAGS.split(' ')

    def _process_test_manifest(self, obj):
        # Much of the logic in this function could be moved to CommonBackend.
        self.backend_input_files.add(mozpath.join(obj.topsrcdir,
            obj.manifest_relpath))

        # Don't allow files to be defined multiple times unless it is allowed.
        # We currently allow duplicates for non-test files or test files if
        # the manifest is listed as a duplicate.
        for source, (dest, is_test) in obj.installs.items():
            try:
                self._install_manifests['tests'].add_symlink(source, dest)
            except ValueError:
                if not obj.dupe_manifest and is_test:
                    raise

        for base, pattern, dest in obj.pattern_installs:
            try:
                self._install_manifests['tests'].add_pattern_symlink(base,
                    pattern, dest)
            except ValueError:
                if not obj.dupe_manifest:
                    raise

        for dest in obj.external_installs:
            try:
                self._install_manifests['tests'].add_optional_exists(dest)
            except ValueError:
                if not obj.dupe_manifest:
                    raise

        m = self._test_manifests.setdefault(obj.flavor,
            (obj.install_prefix, set()))
        m[1].add(obj.manifest_obj_relpath)

        if isinstance(obj.manifest, ReftestManifest):
            # Mark included files as part of the build backend so changes
            # result in re-config.
            self.backend_input_files |= obj.manifest.manifests

    def consume_object(self, obj):
        if not isinstance(obj, ContextDerived):
            return

        srcdir = obj.srcdir

        CommonBackend.consume_object(self, obj)

        if isinstance(obj, DirectoryTraversal):
            self._paths_to_configs[srcdir] = obj.config
            if not self._topdirs_config.has_key(obj.topsrcdir):
                self._compute_xul_flags(obj.config)
                config = obj.config.to_dict()
                self._topdirs_config[obj.topsrcdir] = config
            self._get_config(srcdir)['target'] = obj.target
            self._get_config(srcdir)['topsrcdir'] = obj.topsrcdir
            obj.ack()
        else:
            all_contextes = self._get_config(srcdir).setdefault('all_contextes', [])
            new_context = copy.copy(obj)
            all_contextes.append(new_context)

        if isinstance(obj, TestManifest):
            self._process_test_manifest(obj)

        if obj._ack:
            return

        if isinstance(obj, Sources):
            self._add_sources(srcdir, obj)

        elif isinstance(obj, HostSources):
            self._add_sources(srcdir, obj)

        elif isinstance(obj, GeneratedSources):
            self._add_sources(srcdir, obj)

        elif isinstance(obj, Library):
            self._get_config(srcdir)['library_name'] = obj.library_name
            if hasattr(obj, 'link_into') and obj.link_into:
                self._libs_link_into[obj.basename] = obj.link_into

            if obj.library_name and (isinstance(obj, SharedLibrary) or obj.is_sdk):
                self._top_libs[obj.library_name] = obj
            if isinstance(obj, SharedLibrary) and obj.variant == SharedLibrary.COMPONENT:
                chromeFile = mozpath.join(self.environment.topobjdir, obj.target, 'chrome.manifest')
                jar.addStringToListFile(chromeFile, 'binary-component %s' % obj.soname, self._chrome_set)
            self._libs_to_paths[obj.basename] = srcdir

        elif isinstance(obj, Defines):
            self._paths_to_defines.setdefault(srcdir, {}).update(obj.defines)

        elif isinstance(obj, LocalInclude):
            p = obj.path
            includes = self._paths_to_includes.setdefault(srcdir, [])

            if p.startswith('/'):
                final_include = mozpath.join(obj.topsrcdir, p[1:])
            else:
                final_include = mozpath.join(srcdir, p)
            includes.append(mozpath.normpath(final_include))

        elif isinstance(obj, Exports):
            self._process_exports(obj, obj.exports)

        elif isinstance(obj, GeneratedFile):
            #TODO: no handle this time
            pass

        elif isinstance(obj, VariablePassthru):
            self._process_variable_passthru(obj)

        elif isinstance(obj, JsPreferenceFile):
            target = mozpath.join(obj.target, 'defaults','preferences')
            self._process_files(obj, [obj.path], target, True)

        elif isinstance(obj, ConfigFileSubstitution):
            #TODO: no handle this time
            pass
        elif isinstance(obj, JARManifest):
            exist_defines = self._paths_to_defines.get(srcdir, {})
            defines = compute_defines(obj.config, 'DEFINES', exist_defines)
            chromeDir = mozpath.join(obj.topobjdir, obj.target, 'chrome')

            localedir = srcdir
            if exist_defines.has_key('RELATIVESRCDIR'):
                localedir = mozpath.join(obj.topsrcdir, exist_defines['RELATIVESRCDIR'])
            jarArgs = [
                '-v',
                '-t', obj.topsrcdir,
                '--output-list',
                '-j', chromeDir,
                '-f', 'flat',
                '-c', mozpath.join(localedir, obj.config.substs['AB_CD']),
            ]
            jar.main(jarArgs + self.XULPPFLAGS + defines + [obj.path],
                chromeSet = self._chrome_set)
            jm = jar.jm
            self._add_jar_install_list(obj, jm.installList)
            self._add_jar_install_list(obj, jm.processList, True)
            self.backend_input_files.add(obj.path)

            #$(call py_action,jar_maker, $(QUIET) -j $(FINAL_TARGET)/chrome
            #$(MAKE_JARS_FLAGS) $(XULPPFLAGS) $(DEFINES) $(ACDEFINES) $(JAR_MANIFEST))
            #MAKE_JARS_FLAGS += --root-manifest-entry-appid='$(XPI_ROOT_APPID)'
            #print(self._paths_to_defines[srcdir])
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
        elif isinstance(obj, InstallationTarget):
            #No need to hanlde InstallationTarget
            #print([obj.xpiname, obj.subdir, obj.target])
            pass
        elif isinstance(obj, GeneratedInclude):
            self._process_generated_include(obj)
        else:
            self.typeSet.add(obj.__class__.__name__)

        # Just acknowledge everything.
        obj.ack()

    def _add_sources(self, srcdir, obj):
        s = self._paths_to_sources.setdefault(srcdir, set())
        s.update(obj.files)

    def _process_unified_sources(self, obj):
        srcdir = getattr(obj, 'srcdir', None)

        if obj.have_unified_mapping:
            sources = self._paths_to_unifies
        else:
            sources = self._paths_to_sources
        s = sources.setdefault(srcdir, set())
        s.update(obj.files)
        if obj.have_unified_mapping:
            unified_files = [mozpath.join(obj.objdir, unified_file) for unified_file, _ in obj.unified_source_mapping]
            self._path_to_unified_sources.update(unified_files);

    def _process_generated_include(self, obj):
        if obj.path.startswith('/'):
            path = self.environment.topobjdir.replace('\\', '/') + obj.path
        else:
            path = mozpath.join(obj.srcdir, obj.path)
        path = mozpath.normpath(path).replace('\\', '/')
        srcdirConfig = self._get_config(obj.srcdir)
        srcdirConfig.setdefault('generated_includes', []).append(path)

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
            install_manifest, reltarget = self._get_manifest_from_target('dist/include')
            install_manifest.add_symlink(source, mozpath.join(reltarget, dest))

            if not os.path.exists(source):
                raise Exception('File listed in EXPORTS does not exist: %s' % source)

    def _process_javascript_modules(self, obj):
        if obj.flavor not in ('extra', 'extra_pp', 'testing'):
          raise Exception('Unsupported JavaScriptModules instance: %s' % obj.flavor)

        target = mozpath.join(obj.target, 'modules')
        if obj.flavor == 'extra':
            self._process_final_target_files(obj, obj.modules, target)
            return

        if obj.flavor == 'extra_pp':
            self._process_final_target_files(obj, obj.modules, target, preprocessor=True)
            return

        if not self.environment.substs.get('ENABLE_TESTS', False):
            return

        manifest = self._install_manifests['tests']

        for source, dest, _ in self._walk_hierarchy(obj, obj.modules):
            manifest.add_symlink(source, mozpath.join('modules', dest))

    def _get_manifest_from_target(self, target):
        prefix_list = [
            'dist/bin',
            'dist/idl',
            'dist/include',
            'dist/public',
            'dist/private',
            'dist/sdk',
            'dist/xpi-stage',
            'dist/branding',
            'tests',
            'xpidl',
        ]
        prefix_list = [
            'dist',
            'tests',
            'build',
        ]
        for prefix in prefix_list:
            if target == prefix or target.startswith(prefix + '/'):
                install_manifest = self._install_manifests[prefix.replace('/', '_')]
                reltarget = mozpath.relpath(target, prefix)
                return (install_manifest, reltarget)
        raise Exception("Cannot install to " + target)

    def _process_files(self, obj, files, target, preprocessor = False, marker='#', target_is_file=False, optional=False):
        for f in files:
            if optional:
                full_dest = f
            elif target_is_file:
                full_dest = target
            else:
                full_dest = mozpath.join(target, mozpath.basename(f))
            install_manifest, dest = self._get_manifest_from_target(full_dest)
            source = None if (obj is None) else mozpath.normpath(mozpath.join(obj.srcdir, f))
            if preprocessor:
                dep_file = mozpath.join(self.dep_path, target, mozpath.basename(f) +'.pp')
                exist_defines = self._paths_to_defines.get(obj.srcdir, {})

                xul_defines = dict(exist_defines)
                for flag in self.XULPPFLAGS:
                    if flag.startswith('-D'):
                        define = flag[2:].split('=')
                        xul_defines[define[0]] = define[1] if len(define) >= 2 else ''
                defines = compute_defines(obj.config, defines = xul_defines)
                new_marker = marker
                if marker == 'jar':
                    new_marker = '%' if f.endswith('.css') else '#'
                install_manifest.add_preprocess(source, dest, dep_file, marker=new_marker, defines=defines)
            elif optional:
                install_manifest.add_optional_exists(dest)
            else:
                install_manifest.add_symlink(source, dest)

    def _process_final_target_files(self, obj, files, target, preprocessor=False, marker='#'):
        for path, strings in files.walk():
            self._process_files(obj, strings, mozpath.join(target, path), preprocessor=preprocessor, marker=marker)

    def _process_test_harness_files(self, obj):
        for path, files in obj.srcdir_files.iteritems():
            for source in files:
                dest = '%s/%s' % (path, mozpath.basename(source))
                self._install_manifests['tests'].add_symlink(source, dest)

        for path, patterns in obj.srcdir_pattern_files.iteritems():
            for p in patterns:
                if p[:1] == '/':
                  self._install_manifests['tests'].add_pattern_symlink(obj.topsrcdir, p, path)
                else:
                  self._install_manifests['tests'].add_pattern_symlink(obj.srcdir, p, path)

        for path, files in obj.objdir_files.iteritems():
            for source in files:
                dest = '%s/%s' % (path, mozpath.basename(source))
                print(source, mozpath.join(reltarget, dest))
                test_manifest.add_symlink(source, mozpath.join(reltarget, dest))

    def _get_full_path(self, obj, filename):
        return mozpath.normpath(mozpath.join(obj.srcdir, filename))

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
            if k == 'EXTRA_COMPONENTS' or k == 'EXTRA_PP_COMPONENTS':
                target = mozpath.join(obj.target, 'components')
                files = self._paths_components_files.setdefault(obj.target, [])
                files += v
                self._process_files(obj, v, target, k == 'EXTRA_PP_COMPONENTS')
            elif k == 'PYTHON_UNIT_TESTS':
                for p in v:
                    self._python_unit_tests.add(self._get_full_path(obj, p))
            elif k == 'GARBAGE':
                for p in v:
                    self._garbages.add(self._get_full_path(obj, p))
            elif k in cc_flags:
                self._get_config(obj.srcdir).setdefault('passthru', {})[k] = v
            else:
                print(k, v)

    def print_list(self, v):
        for x in sorted(list(v)):
            print(x)

    def consume_finished(self):
        CommonBackend.consume_finished(self)

        for dist_dir in self._paths_components_files.keys():
            chromeFile = mozpath.join(self.environment.topobjdir, dist_dir, 'chrome.manifest')
            for f in self._paths_components_files[dist_dir]:
                manifestName = mozpath.basename(f)
                if not manifestName.endswith('.manifest'):
                    continue
                jar.addStringToListFile(chromeFile, 'manifest components/%s' % manifestName, self._chrome_set)
        chromeFile = mozpath.join(self.environment.topobjdir, 'dist/bin', 'chrome.manifest')
        jar.addStringToListFile(chromeFile, 'manifest chrome/locales/locales.manifest', self._chrome_set)
        jar.addStringToListFile(chromeFile, 'manifest chromeless/chromeless.manifest', self._chrome_set)
        jar.addStringToListFile(chromeFile, 'manifest app/chrome.manifest', self._chrome_set)

        sdk_path = self.environment.substs['LIBXUL_DIST']

        build_manifest, build_target = self._get_manifest_from_target('build')
        build_manifest.add_symlink(mozpath.join(sdk_path, 'automation.py'), mozpath.join(build_target, 'automation.py'))
        build_manifest.add_optional_exists(mozpath.join(build_target, 'configStatus.py'))
        build_manifest.add_optional_exists(mozpath.join(build_target, 'configStatus.pyc'))

        dist_list = [
            'bin/mozglue.dll',
            'sdk/bin/ply/__init__.py',
            'sdk/bin/ply/lex.py',
            'sdk/bin/ply/yacc.py',
            'sdk/bin/header.py',
            'sdk/bin/typelib.py',
            'sdk/bin/xpidl.py',
            'sdk/bin/xpidllex.py',
            'sdk/bin/xpidlyacc.py',
            'sdk/bin/xpt.py',
        ]

        dist_manifest, target = self._get_manifest_from_target('dist')
        for dist_item in dist_list:
            dist_manifest.add_symlink(mozpath.join(sdk_path, dist_item), mozpath.join(target, dist_item))

        dist_manifest.add_optional_exists(mozpath.join(target, 'bin/.purgecaches'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'bin/wpsmail.exe'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/wpsmail.pdb'))

        dist_manifest.add_optional_exists(mozpath.join(target, 'bin/helper.exe'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/helper.pdb'))

        dist_manifest.add_optional_exists(mozpath.join(target, 'bin/WSEnable.exe'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/WSEnable.pdb'))

        dist_manifest.add_optional_exists(mozpath.join(target, 'bin/mozMapi32.dll'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/mozMapi32.exp'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/mozMapi32.pdb'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/mozMapi32.lib'))

        dist_manifest.add_optional_exists(mozpath.join(target, 'bin/bolt.dll'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/bolt.exp'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/bolt.pdb'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/bolt.lib'))

        dist_manifest.add_optional_exists(mozpath.join(target, 'bin/MapiProxy.dll'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/MapiProxy.exp'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/MapiProxy.pdb'))
        dist_manifest.add_optional_exists(mozpath.join(target, 'lib/MapiProxy.lib'))

        print(self.typeSet)
        #print(self.all_configs)
        #self.print_list(self._garbages)
        #self.print_list(self._python_unit_tests)
        #self.print_list(self.backend_input_files) # moz.build files
        #self.print_list(self._extra_pp_components)
        #self.print_list(self._js_preference_files)

        chrome_files = sorted([mozpath.relpath(p, self.environment.topobjdir) for p in self._chrome_set])
        self._process_files(None, chrome_files, '', optional=True)

        # Make the master test manifest files.
        for flavor, t in self._test_manifests.items():
            install_prefix, manifests = t
            manifest_stem = mozpath.join(install_prefix, '%s.ini' % flavor)
            self._write_master_test_manifest(mozpath.join(
                self.environment.topobjdir, '_tests', manifest_stem),
                manifests)

            # Catch duplicate inserts.
            try:
                self._install_manifests['tests'].add_optional_exists(manifest_stem)
            except ValueError:
                pass

        self._write_manifests('install', self._install_manifests)

        ensureParentDir(mozpath.join(self.environment.topobjdir, 'dist', 'foo'))
        savePickle(self.all_configs_path, self.all_configs)
        self.build()

    def _write_manifests(self, dest, manifests):
        man_dir = mozpath.join(self.environment.topobjdir, '_build_manifests',
            dest)

        # We have a purger for the manifests themselves to ensure legacy
        # manifests are deleted.
        purger = FilePurger()

        for k, manifest in manifests.items():
            purger.add(k)

            with self._write_file(mozpath.join(man_dir, k)) as fh:
                manifest.write(fileobj=fh)

        purger.purge(man_dir)

    def _write_master_test_manifest(self, path, manifests):
        with self._write_file(path) as master:
            master.write(
                '; THIS FILE WAS AUTOMATICALLY GENERATED. DO NOT MODIFY BY HAND.\n\n')

            for manifest in sorted(manifests):
                master.write('[include:%s]\n' % manifest)
    def _get_xpt_path_from_idl_module(self, manager, module):
        install_target, _ = manager.modules[module]
        return mozpath.join(install_target, 'components', module + '.xpt')

    def _handle_idl_manager(self, manager):#For CommonBackend to call
        for idl in manager.idls.values():
            idl_manifest, idl_reltarget = self._get_manifest_from_target('dist/idl')
            idl_manifest.add_symlink(idl['source'], mozpath.join(idl_reltarget, idl['basename']))
            self._idl_set.add(idl['basename'])

            xpt_path = self._get_xpt_path_from_idl_module(manager, idl['module'])
             # These .h files are generated by xpt genearting procedure
            idl_header_manifest, idl_header_reltarget = self._get_manifest_from_target('dist/include')
            header_dest = mozpath.join(idl_header_reltarget, '%s.h' % idl['root'])
            idl_header_manifest.add_optional_exists(header_dest, [mozpath.join(self.environment.topobjdir, xpt_path)])

        xpt_modules = sorted(manager.modules.keys())
        for module in xpt_modules:
            install_target, sources = manager.modules[module]
            deps =[mozpath.join(self.environment.topobjdir, 'dist/idl', p + '.idl') for p in sorted(sources)]

            xpt_path = self._get_xpt_path_from_idl_module(manager, module)
            install_manifest, reltarget = self._get_manifest_from_target(xpt_path)
            install_manifest.add_optional_exists(reltarget)

            dep_file = mozpath.join(self.dep_path, xpt_path + '.pp')
            self._xpt_list.append((xpt_path, deps, dep_file))
            interfaces_path = mozpath.join(self.environment.topobjdir,
                install_target, 'components', 'interfaces.manifest')
            jar.addEntryToListFile(interfaces_path, self._chrome_set)
            jar.addStringToListFile(interfaces_path, 'interfaces {0}'.format(module + '.xpt'), self._chrome_set)

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
                manifest, reltarget = self._get_manifest_from_target('dist/include')
                manifest.add_optional_exists(mozpath.join(reltarget, mozpath.relpath(f, include_dir)))

    @property
    def all_configs_path(self):
        return mozpath.join(self.environment.topobjdir, 'all_config.pickle')

    def load_all_configs(self):
        def new_setitem(self, key, value):
            dict.__setitem__(self, key, value)

        with open(self.all_configs_path, 'rb') as fh:
            import cPickle
            saved_setitem = ReadOnlyDict.__setitem__
            ReadOnlyDict.__setitem__ = new_setitem
            ret = cPickle.load(fh)
            ReadOnlyDict.__setitem__ = saved_setitem
            return ret

    def addDependencies(self, target, deps):
        if not target in self.newDeps:
            self.newDeps[target] = set()
        for p in deps:
            p = mozpath.normpath(p)
            self.newDeps[target].add(p)
            self.sourceFiles.add(p)

    def loadDependencies(self):
        self.newDeps = {}
        self.modifiedTarget = set()
        self.sourceFiles = set()
        self.oldDeps,timeStamps = loadPickle(self.depsJson, ({},{}))

        g = {}
        for u,edges in self.oldDeps.items():
            for v in edges:
                if not v in g:
                    g[v] = []
                g[v].append(u)
            pass

        q =  queue.Queue()
        for p,t in timeStamps.items():
            if not os.path.exists(p) or (os.stat(p).st_mtime != t.st_mtime):
                self.modifiedTarget.add(p)
                q.put(p)
        while not q.empty():
            p = q.get_nowait()
            if not p in g:
                continue
            for v in g[p]:
                if v in self.modifiedTarget:
                    continue
                self.modifiedTarget.add(v)
                q.put(v)

    def dumpDependencies(self):
        timeStamps = {}
        # It's should be here to check the last modification time, for the correctness
        try:
            for p in self.sourceFiles:
                timeStamps[p] = os.stat(p)
        except:
            return
        savePickle(self.depsJson, (self.newDeps, timeStamps))

    def targetNeedBuild(self, targetPath):
        if not os.path.exists(targetPath):
            return True

        if not targetPath in self.modifiedTarget \
            and targetPath in self.oldDeps:
            self.addDependencies(targetPath, self.oldDeps[targetPath])
            return False
        return True

    def build(self):
        print("Start building")
        manifest_install = {
            'build': 'build',
            'dist': 'dist',
            'tests': '_tests',
        }

        COMPLETE = 'From {dest}: Kept {existing} existing; Added/updated {updated}; ' \
            'Removed {rm_files} files and {rm_dirs} directories.'

        config = self.environment
        for d in sorted(manifest_install.keys()):
            dest_dir = mozpath.join(config.topobjdir, manifest_install[d])
            manifests_filepath = mozpath.join(self.manifests_root, d)
            result = process_manifest(dest_dir, [manifests_filepath], remove_all_directory_symlinks=False)
            print(COMPLETE.format(dest=dest_dir,
                existing=result.existing_files_count,
                updated=result.updated_files_count,
                rm_files=result.removed_files_count,
                rm_dirs=result.removed_directories_count))

        self.loadDependencies()

        IDL_PARSER_DIR = mozpath.join(config.substs['top_srcdir'], 'xpcom', 'idl-parser')
        #TODO, generate IDL_PARSER_DIR manually

        sys.path[0:0] = [IDL_PARSER_DIR, self.IDL_PARSER_CACHE_DIR]

        for idl_name in sorted(list(self._idl_set)):
            self.generateXpcomCppHeader(config, idl_name, self.IDL_PARSER_CACHE_DIR)

        for xpt_path, xpt_deps, xpt_dep_file in self._xpt_list:
            target_path = mozpath.join(config.topobjdir, xpt_path)
            self.generateXpcomXpt(config, target_path, xpt_deps, self.IDL_PARSER_CACHE_DIR)

        buildFinished = True
        for backend_file in self.backend_input_files:
            if self.targetNeedBuild(backend_file):
                buildFinished = False
                self.addDependencies(backend_file, [backend_file])
        self.dumpDependencies()
        return buildFinished

    def generateXpcomCppHeader(self, config, filename, cache_dir):
        prefixname = filename[:-4]
        targetFilePath  = mozpath.join(config.topobjdir, 'dist/include', prefixname  + ".h")
        if not self.targetNeedBuild(targetFilePath):
            return

        sourceFilePath = mozpath.join(config.topobjdir, 'dist/idl', filename)

        includePaths = [mozpath.join(config.topobjdir, 'dist/idl'),
            mozpath.join(self.libxul_sdk, 'idl')]
        import xpidl
        import header
        try:
            filename = mozpath.join('../../../dist/idl', filename)
            p = xpidl.IDLParser(outputdir=cache_dir)
            idl = p.parse(open(sourceFilePath).read(), filename=filename)
            idl.resolve(includePaths, p)
            outfd = open(targetFilePath, 'w')
            header.print_header(idl, outfd, filename)
            outfd.close()
            deps = set()
            self.updateIdlDeps(config, idl.deps, deps)
            self.addDependencies(targetFilePath, deps)
            self.addDependencies(targetFilePath, [targetFilePath])
            print('%s -> %s' % (sourceFilePath, targetFilePath))
        except Exception as e:
            print("Failed to generate IDL from %s to %s!" % (sourceFilePath, targetFilePath));
            print(e)

    def generateXpcomXpt(self, config, targetPath, files, cache_dir):
        if not self.targetNeedBuild(targetPath):
            return
        xpts = []
        includePaths = [mozpath.join(config.topobjdir, 'dist/idl'),
            mozpath.join(self.libxul_sdk, 'idl')]

        import xpidl
        import xpt
        import typelib
        deps = set()
        p = xpidl.IDLParser(outputdir=cache_dir)
        for f in files:
            idl_data = open(f).read()
            filename =  mozpath.join('../../../dist/idl', os.path.basename(f))

            idl = p.parse(idl_data, filename = filename)
            idl.resolve(includePaths, p)
            xptIo = io.BytesIO()
            typelib.write_typelib(idl, xptIo, filename = filename)
            xptIo.seek(0)
            xpts.append(xptIo)

            self.updateIdlDeps(config, idl.deps, deps)

        print("Generating %s" % targetPath)
        xpt.xpt_link(xpts).write(targetPath)
        self.addDependencies(targetPath, deps)
        self.addDependencies(targetPath, [targetPath])

    def updateIdlDeps(self, config, inDeps, outDeps):
        for dep in inDeps:
            depFilename = os.path.basename(dep)
            if depFilename in self._idl_set:
                depPath = mozpath.join(config.topobjdir,'dist/idl', depFilename)
            else:
                depPath = mozpath.join(self.libxul_sdk,'idl', depFilename)
            outDeps.add(depPath)

    def full_build(self):
        self._init_with(self.init_all_configs)

    def try_build(self):
        buildFinished = True
        cpu_start = time.clock()

        try:
            loadedThings = self.load_all_configs()
            self._init_with(loadedThings)
            buildFinished = self.build()
        except:
            buildFinished = False

        if not buildFinished:
            self.full_build();
        else:
            cpu_time = time.clock() - cpu_start
            print('Building time is:' + str(cpu_time) + ' seconds')
        return buildFinished

import cPickle
def loadPickle(picklePath, default=None):
    if not os.path.exists(picklePath):
        return default
    try:
        with open(picklePath, 'rb') as fh:
            return cPickle.load(fh)
    except:
        return default

def savePickle(picklePath, content):
    with open(picklePath, 'wb') as fh:
        cPickle.dump(content, fh, -1)

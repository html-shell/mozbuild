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
import mozbuild.jar
import mozpack.path as mozpath

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
    LocalInclude,
    Sources,
    UnifiedSources,
)

from ..util import (
    ensureParentDir,
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

#define_type could be ACDEFINES ALLDEFINES or dict or list
def compute_defines(config, define_type = 'dict', defines=None):
    new_defines = dict(config.defines)
    for x in config.non_global_defines:
        if x in new_defines:
            del new_defines[x]
    if defines != None:
        new_defines.update(defines)
    if define_type == 'dict':
        return new_defines
    l = get_define_list(new_defines)
    if define_type == 'list':
        return l
    if define_type == 'DEFINES':
        return ['-D%s=%s' % (name, value) for (name, value) in l]

    if define_type == 'ACDEFINES':
        return ' '.join(['-D%s=%s' % (name,
            shell_quote(new_defines[name]).replace('$', '$$')) for name in new_defines if new_defines[name]])
    if define_type == 'ALLDEFINES':
        return '\n'.join(sorted(['#define %s %s' % (name,
            new_defines[name]) for name in new_defines]))

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
    def _init(self):
        CommonBackend._init(self)

        json_configs = self.json_configs = {
            'topdirs': {},
            'srcdirs': {},
            'garbages': set(),
            'python_unit_tests': {},

            'paths_to_unifies': {},
            'paths_to_sources': {},
            'path_to_unified_sources': set(),
            'paths_to_includes': {},
            'paths_to_defines': {},
            'libs_to_paths': {},
        }

        self._paths_to_configs = {}

        self._topdirs_config = self.json_configs['topdirs']
        self._garbages = self.json_configs['garbages']
        self._python_unit_tests = json_configs['python_unit_tests']

        self._paths_to_unifies = json_configs['paths_to_unifies']
        self._paths_to_sources = json_configs['paths_to_sources']
        self._path_to_unified_sources  = json_configs['path_to_unified_sources']
        self._paths_to_includes = json_configs['paths_to_includes']
        self._paths_to_defines = json_configs['paths_to_defines']
        self._libs_to_paths = json_configs['libs_to_paths']

        self._install_manifests = {
            k: InstallManifest() for k in [
                'all_manifests']
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
                'xpidl',
            ]}
        '''

        #TODO: Remove the manifest files
        self.dep_path = mozpath.join(self.environment.topobjdir, '_build_manifests', '.deps', 'install')
        self._compute_xul_flags(self.environment)

        def detailed(summary):
            return 'Building with internal backend finished.'
        self.summary.backend_detailed_summary = types.MethodType(detailed,
            self.summary)
        self.typeSet = set()

    def _add_jar_install_list(self, obj, installList, preprocessor = False):
        for s,d in installList:
            target = mozpath.relpath(d, obj.topobjdir)
            self._process_files(obj, [s], target, preprocessor = preprocessor, marker='jar', target_is_file = True)

    def _get_config(self, srcdir):
        return self.json_configs['srcdirs'].setdefault(srcdir, {})

    def _compute_xul_flags(self, config):
        substs = config.substs
        XULPPFLAGS = substs['MOZ_DEBUG_ENABLE_DEFS'] if substs['MOZ_DEBUG'] else substs['MOZ_DEBUG_DISABLE_DEFS']
        self.XULPPFLAGS = XULPPFLAGS.split(' ')

    def consume_object(self, obj):
        srcdir = getattr(obj, 'srcdir', None)

        if not isinstance(obj, DirectoryTraversal) and isinstance(obj, ContextDerived):
            all_contextes = self._get_config(srcdir).setdefault('all_contextes', [])
            context = {}
            context['class_name'] = obj.__class__.__name__

            for k in get_slots(type(obj)):
                if k not in ['topsrcdir', 'topobjdir', 'target', 'config']:
                    v = getattr(obj, k)
                    context[k] = v
            all_contextes.append(context)

        if isinstance(obj, DirectoryTraversal):
            self._paths_to_configs[obj.srcdir] = obj.config
            if not self._topdirs_config.has_key(obj.topsrcdir):
                self._compute_xul_flags(obj.config)
                config = obj.config.to_dict()
                self._topdirs_config[obj.topsrcdir] = config
            self._get_config(srcdir)['target'] = obj.target
            self._get_config(srcdir)['topsrcdir'] = obj.topsrcdir

        elif isinstance(obj, ContextDerived) and CommonBackend.consume_object(self, obj):
            return

        elif isinstance(obj, Sources):
            self._add_sources(srcdir, obj)

        elif isinstance(obj, HostSources):
            self._add_sources(srcdir, obj)

        elif isinstance(obj, GeneratedSources):
            self._add_sources(srcdir, obj)

        elif isinstance(obj, Library):
            self._get_config(srcdir)['library_name'] = obj.library_name
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
            defines = compute_defines(self.environment, 'DEFINES', exist_defines)
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
            mozbuild.jar.main(jarArgs + self.XULPPFLAGS + defines + [obj.path])
            jm = mozbuild.jar.jm
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
            install_manifest, _ = self._get_manifest_from_target('dist/include')
            install_manifest.add_symlink(source, dest)

            if not os.path.exists(source):
                raise Exception('File listed in EXPORTS does not exist: %s' % source)

    def _process_javascript_modules(self, obj):
        target = mozpath.join(obj.target, 'modules')
        if obj.flavor == 'extra':
            self._process_final_target_files(obj, obj.modules, target)
            return

        if obj.flavor == 'extra_pp':
            self._process_final_target_files(obj, obj.modules, target, True)
            return

        if obj.flavor == 'testing':
            manifest, _ = self._get_manifest_from_target('tests')
            for source, dest, _ in self._walk_hierarchy(obj, obj.modules):
                manifest.add_symlink(source, mozpath.join('modules', dest))
            return

        raise Exception('Unsupported JavaScriptModules instance: %s' % obj.flavor)

    def _get_manifest_from_target(self, target):
        return self._install_manifests['all_manifests'], target

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
        for prefix in prefix_list:
            if target == prefix or target.startswith(prefix + '/'):
                install_manifest = self._install_manifests[prefix.replace('/', '_')]
                reltarget = mozpath.relpath(target, prefix)
                return (install_manifest, reltarget)
        raise Exception("Cannot install to " + target)

    def _process_files(self, obj, files, target, preprocessor = False, marker='#', target_is_file=False):
        install_manifest, reltarget = self._get_manifest_from_target(target)
        for f in files:
            source = mozpath.normpath(mozpath.join(obj.srcdir, f))
            dest = reltarget if target_is_file else mozpath.join(reltarget, mozpath.basename(f))
            if preprocessor:
                dep_file = mozpath.join(self.dep_path, target, mozpath.basename(f) +'.pp')
                install_manifest.add_preprocess(source, dest, dep_file, marker=marker)
            else:
                install_manifest.add_symlink(source, dest)

    def _process_final_target_files(self, obj, files, target, preprocessor = False, marker='#'):
        for path, strings in files.walk():
            self._process_files(obj, strings, mozpath.join(target, path), preprocessor = False, marker='#')

    def _process_test_harness_files(self, obj):
        for path, files in obj.srcdir_files.iteritems():
            for source in files:
                dest = '%s/%s' % (path, mozpath.basename(source))
                self._get_manifest_from_target('tests')[0].add_symlink(source, dest)

        for path, patterns in obj.srcdir_pattern_files.iteritems():
            for p in patterns:
                self._get_manifest_from_target('tests')[0].add_pattern_symlink(obj.srcdir, p, path)

        for path, files in obj.objdir_files.iteritems():
            #TODO: no handle this time
            pass

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
        print(self.typeSet)
        #print(self.json_configs)
        #self.print_list(self._garbages)
        #self.print_list(self._python_unit_tests)
        #self.print_list(self.backend_input_files) # moz.build files
        #self.print_list(self._extra_pp_components)
        #self.print_list(self._js_preference_files)

        self._write_manifests('install', self._install_manifests)
        ensureParentDir(mozpath.join(self.environment.topobjdir, 'dist', 'foo'))

        json_config_file_path = mozpath.join(self.environment.topobjdir,
            'all_config.pickle')

        import pickle
        with self._write_file(json_config_file_path) as fh:
            pickle.dump(self.json_configs, fh, -1)

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

    def _handle_idl_manager(self, manager):#For CommonBackend to call
        for idl in manager.idls.values():
            self._get_manifest_from_target('dist/idl')[0].add_symlink(idl['source'],
                idl['basename'])
            self._get_manifest_from_target('dist/include')[0].add_optional_exists('%s.h'
                % idl['root']) # These .h files are generated by xpt genearting procedure
        xpt_modules = sorted(manager.modules.keys())
        for module in xpt_modules:
            install_target, sources = manager.modules[module]
            deps = sorted(sources)

            target = mozpath.join(install_target, 'components')
            install_manifest, reltarget = self._get_manifest_from_target(target)
            xpt_path = mozpath.join(reltarget, module + '.xpt')
            dep_file = mozpath.join(self.dep_path, xpt_path + '.pp')

            #The .idl related .h fiels is also genreated by this preprocess
            install_manifest.add_preprocess(deps, xpt_path, dep_file, marker='xpt')

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
                self._get_manifest_from_target('dist/include')[0].add_optional_exists(
                        mozpath.relpath(f, include_dir))

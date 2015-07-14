# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# This file contains a build backend for generating Visual Studio project
# files.

from __future__ import unicode_literals

import errno
import os
import re
import types
import uuid

import xml.etree.ElementTree as ET
from xml.dom import getDOMImplementation

from mozpack.files import FileFinder

from .common import CommonBackend
from .internal import InternalBackend
from .internal import compute_defines_from_dict
from ..frontend.data import (
    Defines,
    GeneratedSources,
    HostSources,
    Library,
    SharedLibrary,
    LocalInclude,
    Sources,
    UnifiedSources,
)
import mozpack.path as mozpath

MSBUILD_NAMESPACE = 'http://schemas.microsoft.com/developer/msbuild/2003'

def get_id(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name)).upper()

# TODO validate mappings are correct. only 2010 confirmed so far
def visual_studio_product_to_internal_version(version, solution=False):
    if solution:
        if version == '2010':
            return '11.00'
        elif version == '2011':
            return '12.00'
        elif version == '2012':
            return '13.00'
        else:
            raise Exception('Unknown version seen: %s' % version)
    else:
        if version == '2010':
            return '10.00'
        elif version == '2011':
            return '11.00'
        elif version == '2012':
            return '12.00'
        else:
            raise Exception('Unknown version seen: %s' % version)

class VisualStudioBackend(InternalBackend):
    """Generate Visual Studio project files.

    This backend is used to produce Visual Studio projects and a solution
    to foster developing Firefox with Visual Studio.

    This backend is currently considered experimental. There are many things
    not optimal about how it works.

    It's worth noting that lots of file I/O here is not using
    self._write_file(). That's because we need Windows line endings preserved
    and self._write_file() currently opens files in text mode, which behaves
    oddly under MozillaBuild.
    """

    def _init(self):
        InternalBackend._init(self)

        # These should eventually evolve into parameters.
        self._out_dir = os.path.join(self.environment.topobjdir, 'msvc')
        # But making this one a parameter requires testing first.
        self._version = '2010'

        def detailed(summary):
            return 'Generated Visual Studio solution at %s' % (
                os.path.join(self._out_dir, 'mozilla.sln'))

        self.summary.backend_detailed_summary = types.MethodType(detailed,
            self.summary)

    def get_all_linked_libraries(self, obj, all_libs=set()):
        for x in obj.linked_libraries:
            if x in all_libs:
                continue
            all_libs.add(x)
            self.get_all_linked_libraries(x, all_libs)
        return all_libs

    def get_sources_from_libs(self, libs):
        all_sources = {}
        for lib in libs:
            exist_sources = self._paths_to_sources.get(lib.srcdir, set())
            if len(exist_sources) == 0:
                exist_sources = self._paths_to_unifies.get(lib.srcdir, set())
            exist_defines = self._paths_to_defines.get(lib.srcdir, {})
            config = object()
            defines = compute_defines_from_dict(exist_defines, 'DEFINES', prefix='')
            includes = self._paths_to_includes.get(lib.srcdir, [])
            passthru = self._get_config(lib.srcdir).setdefault('passthru', {})
            disableStlWrapping = passthru.get('DISABLE_STL_WRAPPING', False)
            if not disableStlWrapping:
                includes.append('$(XulrunnerDistDir)\\stl_wrappers')
            if lib.library_name and len(exist_sources) == 0:
                print(lib.basename)
            for source in exist_sources:
                source_path = mozpath.join(lib.srcdir, source)
                all_sources[mozpath.normpath(source_path)] = {
                    'defines': defines,
                    'includes': includes,
                }
        return all_sources

    def generate_vs_project_for_lib(self, lib):
        all_libs = self.get_all_linked_libraries(lib)
        all_libs.add(lib)
        all_sources = self.get_sources_from_libs(all_libs)
        vs = VsProject(lib.basename, lib.topsrcdir)
        vs.addFiles(all_sources, 'cpp')
        vs.generate()

    def consume_finished(self):
        InternalBackend.consume_finished(self)
        out_dir = self._out_dir
        try:
            os.makedirs(out_dir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

        for libname,obj in self._top_libs.items():
            if isinstance(obj, SharedLibrary) and \
                obj.variant == SharedLibrary.COMPONENT:
                self.generate_vs_project_for_lib(obj)

        return
        projects = {}

        for lib, path in sorted(self._libs_to_paths.items()):
            config = self._paths_to_configs.get(path, None)
            exist_sources = self._paths_to_sources.get(path, set())
            sources = set();
            for s in exist_sources:
                prefx_path = '$(TopObjDir)' if s in self._path_to_unified_sources else '$(TopSrcDir)'
                sources.add(os.path.normpath(os.path.join(prefx_path, path, s)))

            finder = FileFinder(os.path.join(self.environment.topsrcdir, path),
                find_executables=False)

            headers = [t[0] for t in finder.find('*.h')]
            headers = [os.path.normpath(os.path.join('$(TopSrcDir)',
                path, f)) for f in headers]

            includes = [
                os.path.join('$(TopSrcDir)', path),
                os.path.join('$(TopObjDir)', path),
            ]
            includes.extend(self._paths_to_includes.get(path, []))
            includes.append('$(TopObjDir)\\dist\\include\\nss')
            includes.append('$(TopObjDir)\\dist\\include')

            for v in ('NSPR_CFLAGS', 'NSS_CFLAGS', 'MOZ_JPEG_CFLAGS',
                    'MOZ_PNG_CFLAGS', 'MOZ_ZLIB_CFLAGS', 'MOZ_PIXMAN_CFLAGS'):
                if not config:
                    break

                args = config.substs.get(v, '').split()

                for i, arg in enumerate(args):
                    if arg.startswith('-I'):
                        includes.append(os.path.normpath(arg[2:]))

            # Pull in system defaults.
            includes.append('$(DefaultIncludes)')

            includes = [os.path.normpath(i) for i in includes]

            defines = []
            all_defines = {}
            all_defines.update(self._paths_to_defines.get(path, {}))
            defines.extend(self.environment.defines) # Append global defines
            for k, v in all_defines.items():
                if v is True:
                    defines.append(k)
                else:
                    defines.append('%s=%s' % (k, v))

            basename = 'library_%s' % lib
            project_id = self._write_vs_project(out_dir, basename, lib,
                includes=includes,
                forced_includes=['$(TopObjDir)\\dist\\include\\mozilla-config.h'],
                defines=defines,
                headers=headers,
                sources=sources)

            projects[basename] = (project_id, basename, lib)

        # Generate projects that can be used to build common targets.
        for target in ('export', 'binaries', 'tools', 'full'):
            basename = 'target_%s' % target
            command = '$(SolutionDir)\\mach.bat build'
            if target != 'full':
                command += ' %s' % target

            project_id = self._write_vs_project(out_dir, basename, target,
                build_command=command,
                clean_command='$(SolutionDir)\\mach.bat build clean')

            projects[basename] = (project_id, basename, target)

        # A project that can be used to regenerate the visual studio projects.
        basename = 'target_vs'
        project_id = self._write_vs_project(out_dir, basename, 'visual-studio',
            build_command='$(SolutionDir)\\mach.bat build-backend -b VisualStudio')
        projects[basename] = (project_id, basename, 'visual-studio')

        # A project to run the main application binary.
        app_name = self.environment.substs['MOZ_APP_NAME']
        basename = 'binary_%s' % app_name
        project_id = self._write_vs_project(out_dir, basename, app_name,
            debugger=('$(TopObjDir)\\dist\\bin\\%s.exe' % app_name,
                '-no-remote'))
        projects[basename] = (project_id, basename, app_name)

        # Projects to run other common binaries.
        for app in ['js', 'xpcshell']:
            basename = 'binary_%s' % app
            project_id = self._write_vs_project(out_dir, basename, app,
                debugger=('$(TopObjDir)\\dist\\bin\\%s.exe' % app, ''))
            projects[basename] = (project_id, basename, app)

        # Write out a shared property file with common variables.
        props_path = os.path.join(out_dir, 'mozilla.props')
        with open(props_path, 'wb') as fh:
            self._write_props(fh)

        # Generate some wrapper scripts that allow us to invoke mach inside
        # a MozillaBuild-like environment. We currently only use the batch
        # script. We'd like to use the PowerShell script. However, it seems
        # to buffer output from within Visual Studio (surely this is
        # configurable) and the default execution policy of PowerShell doesn't
        # allow custom scripts to be executed.
        with open(os.path.join(out_dir, 'mach.bat'), 'wb') as fh:
            self._write_mach_batch(fh)

        with open(os.path.join(out_dir, 'mach.ps1'), 'wb') as fh:
            self._write_mach_powershell(fh)

        # Write out a solution file to tie it all together.
        solution_path = os.path.join(out_dir, 'mozilla.sln')
        with open(solution_path, 'wb') as fh:
            self._write_solution(fh, projects)

    def _write_solution(self, fh, projects):
        version = visual_studio_product_to_internal_version(self._version, True)
        # This is a Visual C++ Project type.
        project_type = '8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942'

        # Visual Studio seems to require this header.
        fh.write('Microsoft Visual Studio Solution File, Format Version %s\r\n' %
            version)
        fh.write('# Visual Studio %s\r\n' % self._version)

        binaries_id = projects['target_binaries'][0]

        # Write out entries for each project.
        for key in sorted(projects):
            project_id, basename, name = projects[key]
            path = '%s.vcxproj' % basename

            fh.write('Project("{%s}") = "%s", "%s", "{%s}"\r\n' % (
                project_type, name, path, project_id))

            # Make all libraries depend on the binaries target.
            if key.startswith('library_'):
                fh.write('\tProjectSection(ProjectDependencies) = postProject\r\n')
                fh.write('\t\t{%s} = {%s}\r\n' % (binaries_id, binaries_id))
                fh.write('\tEndProjectSection\r\n')

            fh.write('EndProject\r\n')

        # Write out solution folders for organizing things.

        # This is the UUID you use for solution folders.
        container_id = '2150E333-8FDC-42A3-9474-1A3956D46DE8'

        def write_container(desc):
            cid = get_id(desc.encode('utf-8'))
            fh.write('Project("{%s}") = "%s", "%s", "{%s}"\r\n' % (
                container_id, desc, desc, cid))
            fh.write('EndProject\r\n')

            return cid

        library_id = write_container('Libraries')
        target_id = write_container('Build Targets')
        binary_id = write_container('Binaries')

        fh.write('Global\r\n')

        # Make every project a member of our one configuration.
        fh.write('\tGlobalSection(SolutionConfigurationPlatforms) = preSolution\r\n')
        fh.write('\t\tBuild|Win32 = Build|Win32\r\n')
        fh.write('\tEndGlobalSection\r\n')

        # Set every project's active configuration to the one configuration and
        # set up the default build project.
        fh.write('\tGlobalSection(ProjectConfigurationPlatforms) = postSolution\r\n')
        for name, project in sorted(projects.items()):
            fh.write('\t\t{%s}.Build|Win32.ActiveCfg = Build|Win32\r\n' % project[0])

            # Only build the full build target by default.
            # It's important we don't write multiple entries here because they
            # conflict!
            if name == 'target_full':
                fh.write('\t\t{%s}.Build|Win32.Build.0 = Build|Win32\r\n' % project[0])

        fh.write('\tEndGlobalSection\r\n')

        fh.write('\tGlobalSection(SolutionProperties) = preSolution\r\n')
        fh.write('\t\tHideSolutionNode = FALSE\r\n')
        fh.write('\tEndGlobalSection\r\n')

        # Associate projects with containers.
        fh.write('\tGlobalSection(NestedProjects) = preSolution\r\n')
        for key in sorted(projects):
            project_id = projects[key][0]

            if key.startswith('library_'):
                container_id = library_id
            elif key.startswith('target_'):
                container_id = target_id
            elif key.startswith('binary_'):
                container_id = binary_id
            else:
                raise Exception('Unknown project type: %s' % key)

            fh.write('\t\t{%s} = {%s}\r\n' % (project_id, container_id))
        fh.write('\tEndGlobalSection\r\n')

        fh.write('EndGlobal\r\n')

    def _write_props(self, fh):
        impl = getDOMImplementation()
        doc = impl.createDocument(MSBUILD_NAMESPACE, 'Project', None)

        project = doc.documentElement
        project.setAttribute('xmlns', MSBUILD_NAMESPACE)
        project.setAttribute('ToolsVersion', '4.0')

        ig = project.appendChild(doc.createElement('ImportGroup'))
        ig.setAttribute('Label', 'PropertySheets')

        pg = project.appendChild(doc.createElement('PropertyGroup'))
        pg.setAttribute('Label', 'UserMacros')

        ig = project.appendChild(doc.createElement('ItemGroup'))

        def add_var(k, v):
            e = pg.appendChild(doc.createElement(k))
            e.appendChild(doc.createTextNode(v))

            e = ig.appendChild(doc.createElement('BuildMacro'))
            e.setAttribute('Include', k)

            e = e.appendChild(doc.createElement('Value'))
            e.appendChild(doc.createTextNode('$(%s)' % k))

        add_var('TopObjDir', os.path.normpath(self.environment.topobjdir))
        add_var('TopSrcDir', os.path.normpath(self.environment.topsrcdir))
        add_var('PYTHON', '$(TopObjDir)\\_virtualenv\\Scripts\\python.exe')
        add_var('MACH', '$(TopSrcDir)\\mach')

        # From MozillaBuild.
        add_var('DefaultIncludes', os.environ.get('INCLUDE', ''))

        fh.write(b'\xef\xbb\xbf')
        doc.writexml(fh, addindent='  ', newl='\r\n')

    def _relevant_environment_variables(self):
        # Write out the environment variables, presumably coming from
        # MozillaBuild.
        for k, v in sorted(os.environ.items()):
            if not re.match('^[a-zA-Z0-9_]+$', k):
                continue

            if k in ('OLDPWD', 'PS1'):
                continue

            if k.startswith('_'):
                continue

            yield k, v

        yield 'TOPSRCDIR', self.environment.topsrcdir
        yield 'TOPOBJDIR', self.environment.topobjdir

    def _write_mach_powershell(self, fh):
        for k, v in self._relevant_environment_variables():
            fh.write(b'$env:%s = "%s"\r\n' % (k, v))

        relpath = os.path.relpath(self.environment.topsrcdir,
            self.environment.topobjdir).replace('\\', '/')

        fh.write(b'$bashargs = "%s/mach", "--log-no-times"\r\n' % relpath)
        fh.write(b'$bashargs = $bashargs + $args\r\n')

        fh.write(b"$expanded = $bashargs -join ' '\r\n")
        fh.write(b'$procargs = "-c", $expanded\r\n')

        fh.write(b'Start-Process -WorkingDirectory $env:TOPOBJDIR '
            b'-FilePath $env:MOZILLABUILD\\msys\\bin\\bash '
            b'-ArgumentList $procargs '
            b'-Wait -NoNewWindow\r\n')

    def _write_mach_batch(self, fh):
        """Write out a batch script that builds the tree.

        The script "bootstraps" into the MozillaBuild environment by setting
        the environment variables that are active in the current MozillaBuild
        environment. Then, it builds the tree.
        """
        for k, v in self._relevant_environment_variables():
            fh.write(b'SET "%s=%s"\r\n' % (k, v))

        fh.write(b'cd %TOPOBJDIR%\r\n')

        # We need to convert Windows-native paths to msys paths. Easiest way is
        # relative paths, since munging c:\ to /c/ is slightly more
        # complicated.
        relpath = os.path.relpath(self.environment.topsrcdir,
            self.environment.topobjdir).replace('\\', '/')

        # We go through mach because it has the logic for choosing the most
        # appropriate build tool.
        fh.write(b'"%%MOZILLABUILD%%\\msys\\bin\\bash" '
            b'-c "%s/mach --log-no-times %%1 %%2 %%3 %%4 %%5 %%6 %%7"' % relpath)

    def _write_vs_project(self, out_dir, basename, name, **kwargs):
        root = '%s.vcxproj' % basename
        with open(os.path.join(out_dir, root), 'wb') as fh:
            project_id, name = VisualStudioBackend.write_vs_project(fh,
                self._version, name, **kwargs)

        with open(os.path.join(out_dir, '%s.user' % root), 'w') as fh:
            fh.write('<?xml version="1.0" encoding="utf-8"?>\r\n')
            fh.write('<Project ToolsVersion="4.0" xmlns="%s">\r\n' %
                MSBUILD_NAMESPACE)
            fh.write('</Project>\r\n')

        return project_id

    @staticmethod
    def write_vs_project(fh, version, name, includes=[],
        forced_includes=[], defines=[],
        build_command=None, clean_command=None,
        debugger=None, headers=[], sources=[]):

        project_id = get_id(name.encode('utf-8'))

        impl = getDOMImplementation()
        doc = impl.createDocument(MSBUILD_NAMESPACE, 'Project', None)

        project = doc.documentElement
        project.setAttribute('DefaultTargets', 'Build')
        project.setAttribute('ToolsVersion', '4.0')
        project.setAttribute('xmlns', MSBUILD_NAMESPACE)

        ig = project.appendChild(doc.createElement('ItemGroup'))
        ig.setAttribute('Label', 'ProjectConfigurations')

        pc = ig.appendChild(doc.createElement('ProjectConfiguration'))
        pc.setAttribute('Include', 'Build|Win32')

        c = pc.appendChild(doc.createElement('Configuration'))
        c.appendChild(doc.createTextNode('Build'))

        p = pc.appendChild(doc.createElement('Platform'))
        p.appendChild(doc.createTextNode('Win32'))

        pg = project.appendChild(doc.createElement('PropertyGroup'))
        pg.setAttribute('Label', 'Globals')

        n = pg.appendChild(doc.createElement('ProjectName'))
        n.appendChild(doc.createTextNode(name))

        k = pg.appendChild(doc.createElement('Keyword'))
        k.appendChild(doc.createTextNode('MakeFileProj'))

        g = pg.appendChild(doc.createElement('ProjectGuid'))
        g.appendChild(doc.createTextNode('{%s}' % project_id))

        rn = pg.appendChild(doc.createElement('RootNamespace'))
        rn.appendChild(doc.createTextNode('mozilla'))

        i = project.appendChild(doc.createElement('Import'))
        i.setAttribute('Project', '$(VCTargetsPath)\\Microsoft.Cpp.Default.props')

        ig = project.appendChild(doc.createElement('ImportGroup'))
        ig.setAttribute('Label', 'ExtensionTargets')

        ig = project.appendChild(doc.createElement('ImportGroup'))
        ig.setAttribute('Label', 'ExtensionSettings')

        ig = project.appendChild(doc.createElement('ImportGroup'))
        ig.setAttribute('Label', 'PropertySheets')
        i = ig.appendChild(doc.createElement('Import'))
        i.setAttribute('Project', 'mozilla.props')

        pg = project.appendChild(doc.createElement('PropertyGroup'))
        pg.setAttribute('Label', 'Configuration')
        ct = pg.appendChild(doc.createElement('ConfigurationType'))
        ct.appendChild(doc.createTextNode('Makefile'))

        pg = project.appendChild(doc.createElement('PropertyGroup'))
        pg.setAttribute('Condition', "'$(Configuration)|$(Platform)'=='Build|Win32'")

        if build_command:
            n = pg.appendChild(doc.createElement('NMakeBuildCommandLine'))
            n.appendChild(doc.createTextNode(build_command))

        if clean_command:
            n = pg.appendChild(doc.createElement('NMakeCleanCommandLine'))
            n.appendChild(doc.createTextNode(clean_command))

        if includes:
            n = pg.appendChild(doc.createElement('NMakeIncludeSearchPath'))
            n.appendChild(doc.createTextNode(';'.join(includes)))
            n = pg.appendChild(doc.createElement('IncludePath'))
            n.appendChild(doc.createTextNode(';'.join(includes)))

        if forced_includes:
            n = pg.appendChild(doc.createElement('NMakeForcedIncludes'))
            n.appendChild(doc.createTextNode(';'.join(forced_includes)))

        if defines:
            n = pg.appendChild(doc.createElement('NMakePreprocessorDefinitions'))
            n.appendChild(doc.createTextNode(';'.join(defines)))

        if debugger:
            n = pg.appendChild(doc.createElement('LocalDebuggerCommand'))
            n.appendChild(doc.createTextNode(debugger[0]))

            n = pg.appendChild(doc.createElement('LocalDebuggerCommandArguments'))
            n.appendChild(doc.createTextNode(debugger[1]))

        i = project.appendChild(doc.createElement('Import'))
        i.setAttribute('Project', '$(VCTargetsPath)\\Microsoft.Cpp.props')

        i = project.appendChild(doc.createElement('Import'))
        i.setAttribute('Project', '$(VCTargetsPath)\\Microsoft.Cpp.targets')

        # Now add files to the project.
        ig = project.appendChild(doc.createElement('ItemGroup'))
        for header in sorted(headers or []):
            n = ig.appendChild(doc.createElement('ClInclude'))
            n.setAttribute('Include', header)

        ig = project.appendChild(doc.createElement('ItemGroup'))
        for source in sorted(sources or []):
            n = ig.appendChild(doc.createElement('ClCompile'))
            n.setAttribute('Include', source)

        fh.write(b'\xef\xbb\xbf')
        doc.writexml(fh, addindent='  ', newl='\r\n')

        return project_id, name

def indent(elem, level=0):
    i = "\n" + level*"  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            indent(elem, level+1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

class VsProject(object):
    def __init__(self, name, srcDir):
        self.name = name
        self.srcDir = srcDir
        self.files = {
            'cpp':{},
            'h': {},
            'text':{},
        }

    def addFiles(self, files, fileType):
        if not fileType in self.files:
            self.files[fileType] = []
        self.files[fileType].update(files)

    def findHeaders(self):
        for f in self.files['cpp']:
            d = os.path.dirname(f)
            for p in os.listdir(d):
                if p.endswith('.h'):
                    pass
            pass

    def generateCppInclude(self, itemGroup, filePath, fileInfo):
#                            'defines': defines,
#                            'includes': includes,

        cl = ET.SubElement(itemGroup, 'ClCompile', {'Include':os.path.relpath(filePath, self.srcDir)})
        includes = list(fileInfo['includes'])
        if (len(includes) > 0):
            includes.append('%(AdditionalIncludeDirectories)')
            ET.SubElement(cl, 'AdditionalIncludeDirectories').text = ';'.join(includes)
        defines = list(fileInfo['defines'])
        if (len(defines) > 0):
            defines.append('%(PreprocessorDefinitions)')
            ET.SubElement(cl, 'PreprocessorDefinitions').text = ';'.join(defines)

    def generateItemGroup(self, itemGroup):
        for fileType, files in self.files.iteritems():
            if fileType == 'cpp':
                for filePath in sorted(files.keys()):
                    self.generateCppInclude(itemGroup, filePath, files[filePath])
                pass

            pass

    def generate(self):
        self.findHeaders()
        vs = ET.Element('Project')
        vs.attrib['xmlns'] = "http://schemas.microsoft.com/developer/msbuild/2003"
        self.generateItemGroup(ET.SubElement(vs, 'ItemGroup'))

        indent(vs)
        ET.ElementTree(vs).write(os.path.join(self.srcDir, self.name + '.props'), encoding = "utf-8")
        pass

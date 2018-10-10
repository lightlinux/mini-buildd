# -*- coding: utf-8 -*-

import os
import glob
import subprocess
import distutils.command.clean

import setuptools
import setuptools.command.build_py
import debian.changelog


# Get version from debian/changelog
MINI_BUILDD_VERSION = str(debian.changelog.Changelog(file=open("./debian/changelog", "rb")).version)
MINI_BUILDD_GENFILES = {"init": "src/mini_buildd/__init__.py", "man8": "src/mini-buildd.8", "man1": "src/mini-buildd-tool.1"}


class BuildPy(setuptools.command.build_py.build_py):
    def run(self):
        print("I: Generating {}...".format(MINI_BUILDD_GENFILES["init"]))
        with open(MINI_BUILDD_GENFILES["init"], "w", encoding="UTF-8") as init_py:
            init_py.write("""\
# -*- coding: utf-8 -*-

__version__ = "{version}"
""".format(version=MINI_BUILDD_VERSION))

        print("I: Generating {}...".format(MINI_BUILDD_GENFILES["man8"]))
        subprocess.check_call("help2man --no-info --section 8 --include ./src/mini-buildd.help2man.include --output {} ./src/mini-buildd".format(MINI_BUILDD_GENFILES["man8"]), shell=True)

        print("I: Generating {}...".format(MINI_BUILDD_GENFILES["man1"]))
        subprocess.check_call("help2man --no-info --section 1 --include ./src/mini-buildd-tool.help2man.include --output {} ./src/mini-buildd-tool".format(MINI_BUILDD_GENFILES["man1"]), shell=True)

        super().run()


class Clean(distutils.command.clean.clean):
    def run(self):
        for f in MINI_BUILDD_GENFILES.values():
            if os.path.exists(f):
                print("I: Cleaning {}...".format(f))
                os.remove(f)

        super().run()


def package_data_files(directory, extensions):
    """
    Little helper to collect file lists for package_data.
    """
    package_path = "src/mini_buildd"
    result = []
    for extension in extensions:
        result += [f[len(package_path) + 1:] for f in glob.glob("{}/{}/**/*.{}".format(package_path, directory, extension), recursive=True)]
    return result


setuptools.setup(
    cmdclass={"build_py": BuildPy, "clean": Clean},
    name="mini-buildd",
    version=MINI_BUILDD_VERSION,
    package_dir={'': 'src'},
    description="Mini Debian build daemon",
    author="Stephan Sürken",
    author_email="absurd@debian.org",
    scripts=["src/mini-buildd", "src/mini-buildd-tool"],
    packages=["mini_buildd", "mini_buildd/api", "mini_buildd/models"],
    package_data={"mini_buildd":
                  package_data_files("templates", ["html", "txt"])
                  + package_data_files("templatetags", ["py"])
                  + package_data_files("static", ["css", "js", "png", "gif", "ico"])})

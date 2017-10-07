# -*- coding: utf-8 -*-

import os
import subprocess
import contextlib
import distutils.command.clean

import setuptools
import setuptools.command.build_py
import debian.changelog


# Get version from debian/changelog
MINI_BUILDD_VERSION = str(debian.changelog.Changelog(file=open("./debian/changelog", "rb")).version)
MINI_BUILDD_MANPAGES = ["mini-buildd-tool.1", "mini-buildd.8"]


class BuildPy(setuptools.command.build_py.build_py):
    @classmethod
    def _gen_init(cls):
        path = "./src/mini_buildd/__init__.py"
        print("I: Generating {f} ({v})...".format(f=path, v=MINI_BUILDD_VERSION))

        with open(path, "w", encoding="UTF-8") as init_py:
            init_py.write("""\
# -*- coding: utf-8 -*-

__version__ = "{version}"
""".format(version=MINI_BUILDD_VERSION))

    def _build_man(self, man):
        build_base = os.path.dirname(self.build_lib)
        name, dummy, section = man.partition(".")
        output = os.path.join(build_base, man)

        print("I: Generating \"{}\"...".format(output))
        os.makedirs(build_base, exist_ok=True)
        subprocess.check_call(["help2man",
                               "--no-info",
                               "--section", section,
                               "--include", "./src/{}.help2man.include".format(name),
                               "--output", output,
                               "./src/{}".format(name)])

    def run(self):
        self._gen_init()
        for man in MINI_BUILDD_MANPAGES:
            self._build_man(man)
        super().run()


class Clean(distutils.command.clean.clean):
    def _clean_man(self, man):
        with contextlib.suppress(FileNotFoundError):
            f = os.path.join(self.build_base, man)
            print("I: Cleaning {}...".format(f))
            os.remove(f)

    def run(self):
        for man in MINI_BUILDD_MANPAGES:
            self._clean_man(man)
        super().run()


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
    package_data={"mini_buildd": ["templates/*.html",
                                  "templates/includes/*.html",
                                  "templates/mini_buildd/*.html",
                                  "templates/admin/*.html",
                                  "templates/admin/mini_buildd/*.html",
                                  "templates/registration/*.html",
                                  "templates/registration/*.txt",
                                  "templatetags/*.py",
                                  "static/css/*.css",
                                  "static/js/*.js",
                                  "static/img/*.png",
                                  "static/img/*.gif",
                                  "static/*.*"]})

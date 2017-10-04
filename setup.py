# -*- coding: utf-8 -*-

import os
import subprocess

import setuptools
import setuptools.command.build_py
import debian.changelog


# Get version from debian/changelog
MINI_BUILDD_VERSION = str(debian.changelog.Changelog(file=open("./debian/changelog", "rb")).version)


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

    @classmethod
    def _gen_man(cls, name, section):
        print("I: Generating man page for \"{n}\"...".format(n=name))
        os.makedirs("./build", exist_ok=True)
        subprocess.check_call(["help2man",
                               "--no-info",
                               "--section", section,
                               "--include", "./src/{}.help2man.include".format(name),
                               "--output", "./build/{}.{}".format(name, section),
                               "./src/{}".format(name)])

    def run(self):
        self._gen_init()
        self._gen_man("mini-buildd", "8")
        self._gen_man("mini-buildd-tool", "1")
        super().run()


setuptools.setup(
    cmdclass={"build_py": BuildPy},
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

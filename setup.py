# -*- coding: utf-8 -*-

import subprocess

import setuptools
import setuptools.command.build_py
import debian.changelog
import sphinx.setup_command


# Get version from debian/changelog, and update package's __init__.py unconditionally
MINI_BUILDD_VERSION = str(debian.changelog.Changelog(file=open("./debian/changelog", "rb")).version)
MINI_BUILDD_INIT_PY_PATH = "./src/mini_buildd/__init__.py"
MINI_BUILDD_INIT_PY = """\
# -*- coding: utf-8 -*-

__version__ = "{version}"
""".format(version=MINI_BUILDD_VERSION)


class BuildDoc(sphinx.setup_command.BuildDoc):
    def finalize_options(self):
        # pylint: disable=attribute-defined-outside-init
        self.build_dir = self.build_dir if self.build_dir else "./build/sphinx"
        self.source_dir = self.source_dir if self.source_dir else "./doc/"

        super().finalize_options()

    def run(self):
        super().run()


class BuildPy(setuptools.command.build_py.build_py):
    @classmethod
    def _gen_manpage(cls, name, section):
        subprocess.check_call(["help2man",
                               "--no-info",
                               "--section", section,
                               "--include", "./src/{}.help2man.include".format(name),
                               "--output", "./src/{}.{}".format(name, section),
                               "./src/{}".format(name)])

    def run(self):
        with open(MINI_BUILDD_INIT_PY_PATH, "w", encoding="UTF-8") as init_py:
            init_py.write(MINI_BUILDD_INIT_PY)
        print("I: Updated {f} ({v})".format(f=MINI_BUILDD_INIT_PY_PATH, v=MINI_BUILDD_VERSION))

        super().run()
        self.run_command("build_sphinx")
        self._gen_manpage("mini-buildd", "8")
        self._gen_manpage("mini-buildd-tool", "1")


setuptools.setup(
    cmdclass={"build_sphinx": BuildDoc,
              "build_py": BuildPy},
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

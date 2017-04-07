# -*- coding: utf-8 -*-

import os
import shutil
import subprocess

import setuptools
import debian.changelog
import sphinx.setup_command


# Get version from debian/changelog, and update package's __init__.py unconditionally
MINI_BUILDD_VERSION = str(debian.changelog.Changelog(file=open("./debian/changelog", "rb")).version)
MINI_BUILDD_INIT_PY = """\
# -*- coding: utf-8 -*-

__version__ = "{version}"
""".format(version=MINI_BUILDD_VERSION)


def update_init(init_py_path="./src/mini_buildd/__init__.py"):
    with open(init_py_path, "w", encoding="UTF-8") as init_py:
        init_py.write(MINI_BUILDD_INIT_PY)
    print("I: Updated {f} ({v})".format(f=init_py_path, v=MINI_BUILDD_VERSION))


class BuildDoc(sphinx.setup_command.BuildDoc):
    def finalize_options(self):
        # pylint: disable=attribute-defined-outside-init
        self.build_dir = self.build_dir if self.build_dir else "./build/sphinx"
        self.source_dir = self.source_dir if self.source_dir else "./build/sphinx"

        # Prepare build dir: doc/, plus static files from app.mini_buildd
        shutil.rmtree(self.build_dir, ignore_errors=True)
        shutil.copytree("./doc", self.build_dir)
        shutil.copytree("./src/mini_buildd/static", os.path.join(self.build_dir, "_static"))

        # Generate API documentation
        subprocess.check_call(["/usr/bin/sphinx-apidoc", "--force", "--output-dir", self.build_dir, "./src/mini_buildd/"])

        super().finalize_options()

    def run(self):
        # Generate man pages via help2man
        subprocess.check_call(["help2man",
                               "--no-info",
                               "--output=" + self.build_dir + "/mini-buildd.8", "--section=8",
                               "--include=doc/mini-buildd.help2man.include", "./src/mini-buildd"])
        subprocess.check_call(["help2man",
                               "--no-info",
                               "--output=" + self.build_dir + "/mini-buildd-tool.1", "--section=1",
                               r"--name=mini-buildd-tool \- User/client tool box for mini-buildd instances.", "./src/mini-buildd-tool"])

        super().run()


update_init()

setuptools.setup(
    cmdclass={"build_sphinx": BuildDoc},
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

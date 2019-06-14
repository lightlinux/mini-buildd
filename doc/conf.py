# -*- coding: utf-8 -*-
#
# http://www.sphinx-doc.org/en/stable/config.html
#
# pylint: disable=C0413,C0103,W0622  # Disable for file: These checks just can't be satisfied here.
#
import sys
import os
import subprocess

sys.path.insert(0, os.path.abspath('../src'))

# Autogenerate API documentation for package mini_buildd
# Strange to do this here, but it (works and) seems more coherent than extra support in setup.py
subprocess.check_call(["sphinx-apidoc", "--force", "--separate", "--output-dir", "./code/mini_buildd/", "../src/mini_buildd/"])

# Pseudo-configure django
import mini_buildd.django_settings
mini_buildd.django_settings.pseudo_configure()

# do not import mini-buildd's version before path insertion -- otherwise
# sphinx-build terminates with following message: "error: no such option: -b"
from mini_buildd import __version__

extensions = ['sphinx.ext.autodoc', 'sphinx.ext.todo', 'sphinx.ext.graphviz']
templates_path = ['_templates']
source_suffix = '.rst'
master_doc = 'index'
project = 'mini-buildd'
copyright = '2012-2017, mini-buildd maintainers'
version = __version__
release = __version__
exclude_patterns = ['_build']
pygments_style = 'sphinx'
html_theme = 'default'
html_static_path = [os.path.abspath("../src/mini_buildd/static")]
htmlhelp_basename = 'mini-buildddoc'

# Extension 'todo'
todo_include_todos = True

# reproducibility: Without this option, todo will add absolute build-time document paths
todo_link_only = True

# Order members like in source (sphinx default (strangely enough) seems to be alphabetic order).
autodoc_member_order = 'bysource'

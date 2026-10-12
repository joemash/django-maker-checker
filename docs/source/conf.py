"""Sphinx configuration for the django-maker-checker documentation."""

import os
import sys
from pathlib import Path

import django

# The API reference imports the package's models, so Django is set up with the library's own test settings.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
django.setup()

import django_maker_checker  # noqa: E402

# -- Project information

project = "django-maker-checker"
author = "Josephat Macharia"
copyright = "2026, Josephat Macharia"
release = django_maker_checker.__version__
version = ".".join(release.split(".")[:2])

# -- General configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "myst_parser",
]

source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
autosectionlabel_prefix_document = True
# Label page titles and top-level sections only: the changelog repeats headings such as "Added" per version.
autosectionlabel_maxdepth = 2
autodoc_member_order = "bysource"
autodoc_default_options = {"show-inheritance": True}
autodoc_typehints = "description"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "django": ("https://docs.djangoproject.com/en/stable/", "https://docs.djangoproject.com/en/stable/_objects/"),
}

templates_path = ["_templates"]
exclude_patterns = ["_build"]

# -- Options for HTML output

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_theme_options = {"navigation_depth": 3}

# -- Options for EPUB output

epub_show_urls = "footnote"

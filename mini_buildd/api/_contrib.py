# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import urllib2

import bs4

class DebianPackageTracker():
    _URL = "https://tracker.debian.org"
    def __init__(self, src_package):
        url = "{b}/pkg/{p}".format(b=self._URL, p=src_package)
        self._page = urllib2.urlopen(url).read()
        self.versions = {}

        soup = bs4.BeautifulSoup(self._page)
        version_tags = soup.findAll("span", { "class" : "versions-repository" })
        for d in version_tags:
            codename = d['title'].split("(")[1].split(" ")[0].translate({ord(")"): None})
            version = d.find_next_sibling("a").contents[0]
            self.versions[codename] = {"version": version,
                                       "changelog_url": "{b}/media/packages/{h}/{p}/changelog-{v}".format(b=self._URL,
                                                                                                          h=src_package[:4] if src_package.startswith("lib") else src_package[0],
                                                                                                          p=src_package,
                                                                                                          v=version)}

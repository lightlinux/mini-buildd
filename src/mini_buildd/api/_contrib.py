"""
Extra, independent code, merely arbitrarily stuffed here.

This is not part of mini-buildd.

"""

import urllib.request
import urllib.parse
import urllib.error

import bs4


class DebianPackageTracker():
    """
    Get (some) source package information from the Debian Package Tracker.

    As long as there is no proper API, we do this the
    hacky way parsing the HTML via bs4 - so this will most
    definitely break at some point. Use at own discretion.
    """

    def __init__(self, src_package, tracker_url="https://tracker.debian.org"):
        self.info = {}

        pkg_url = "{b}/pkg/{p}".format(b=tracker_url, p=src_package)
        soup = bs4.BeautifulSoup(urllib.request.urlopen(pkg_url).read())
        version_tags = soup.findAll("span", {"class": "versions-repository"})
        for d in version_tags:
            codename = d['title'].split("(")[1].split(" ")[0].translate({ord(")"): None})
            version = d.find_next_sibling("a").get_text("", strip=True)
            self.info[codename] = {"version": version,
                                   "changelog_url": "{b}/media/packages/{h}/{p}/changelog-{v}".format(b=tracker_url,
                                                                                                      h=src_package[:4] if src_package.startswith("lib") else src_package[0],
                                                                                                      p=src_package,
                                                                                                      v=version)}

    def get_info(self, codename):
        return self.info[codename]

    def get_version(self, codename):
        return self.info[codename]["version"]

# -*- coding: utf-8 -*-
import os
import re
import subprocess
import logging

import mini_buildd.changes
import mini_buildd.globals
import mini_buildd.misc

log = logging.getLogger(__name__)

class Build():
    def __init__(self, br):
        self._br = br

    def results_from_buildlog(self, fn, changes):
        regex = re.compile("^[a-zA-Z0-9-]+: [^ ]+$")
        with open(fn) as f:
            for l in f:
                if regex.match(l):
                    log.debug("Build log line detected as build status: {l}".format(l=l.strip()))
                    s = l.split(":")
                    changes["Sbuild-" + s[0]] = s[1].strip()

    def run(self):
        """
        .. todo:: Builder

           - DEB_BUILD_OPTIONS
           - [.sbuildrc] proper ccache support (was: Add path for ccache)
           - [.sbuildrc] gpg setup
           - chroot-setup-command: uses sudo workaround (schroot bug).
        """
        pkg_info = "{s}-{v}:{a}".format(s=self._br["Source"], v=self._br["Version"], a=self._br["Architecture"])

        path = self._br.get_spool_dir(mini_buildd.globals.BUILDS_DIR)
        self._br.untar(path=path)

        # Generate .sbuildrc for this run (not all is configurable via switches).
        open(os.path.join(path, ".sbuildrc"), 'w').write("""
# Set "user" mode explicitely (already default).  Means the
# retval tells us if the sbuild run was ok. We also dont have to
# configure "mailto".
$sbuild_mode = 'user';

# We update sources.list on the fly via chroot-setup commands;
# this update occurs before, so we dont need it.
$apt_update = 0;

# Allow unauthenticated apt toggle
$apt_allow_unauthenticated = {apt_allow_unauthenticated};

#$path = '/usr/lib/ccache:/usr/sbin:/usr/bin:/sbin:/bin:/usr/X11R6/bin:/usr/games';
##$build_environment = {{ 'CCACHE_DIR' => '$HOME/.ccache' }};

# Builder identity
$pgp_options = ['-us', '-k Mini-Buildd Automatic Signing Key'];

# don't remove this, Perl needs it:
1;
""".format(apt_allow_unauthenticated=self._br["Apt-Allow-Unauthenticated"]))

        env = os.environ
        env["HOME"] = path

        sbuild_cmd = ["sbuild",
                      "--dist={0}".format(self._br["Distribution"]),
                      "--arch={0}".format(self._br["Architecture"]),
                      "--chroot=mini-buildd-{d}-{a}".format(d=self._br["Base-Distribution"], a=self._br["Architecture"]),
                      "--chroot-setup-command=sudo cp {p}/apt_sources.list /etc/apt/sources.list".format(p=path),
                      "--chroot-setup-command=sudo cp {p}/apt_preferences /etc/apt/preferences".format(p=path),
                      "--chroot-setup-command=sudo apt-get update",
                      "--build-dep-resolver={r}".format(r=self._br["Build-Dep-Resolver"]),
                      "--verbose", "--nolog", "--log-external-command-output", "--log-external-command-error"]

        if "Arch-All" in self._br:
            sbuild_cmd.append("--arch-all")
            sbuild_cmd.append("--source")

        if "Run-Lintian" in self._br:
            sbuild_cmd.append("--run-lintian")
            sbuild_cmd.append("--lintian-opts=--suppress-tags=bad-distribution-in-changes-file")
            sbuild_cmd.append("--lintian-opts={o}".format(o=self._br["Run-Lintian"]))

        if mini_buildd.globals.DEBUG:
            sbuild_cmd.append("--verbose")

        sbuild_cmd.append("{s}_{v}.dsc".format(s=self._br["Source"], v=self._br["Version"]))

        buildlog = os.path.join(path, "{s}_{v}_{a}.buildlog".format(s=self._br["Source"], v=self._br["Version"], a=self._br["Architecture"]))
        log.info("{p}: Starting sbuild".format(p=pkg_info))
        log.debug("{p}: Sbuild options: {c}".format(p=pkg_info, c=sbuild_cmd))
        with open(buildlog, "w") as l:
            retval = subprocess.call(sbuild_cmd,
                                     cwd=path, env=env,
                                     stdout=l, stderr=subprocess.STDOUT)

        res = mini_buildd.changes.Changes(os.path.join(path,
                                                       "{s}_{v}_mini-buildd-buildresult_{a}.changes".
                                                       format(s=self._br["Source"], v=self._br["Version"], a=self._br["Architecture"])))
        for v in ["Distribution", "Source", "Version"]:
            res[v] = self._br[v]

        # Add build results to build request object
        res["Sbuildretval"] = retval
        self.results_from_buildlog(buildlog, res)

        log.info("{p}: Sbuild finished: Sbuildretval={r}, Status={s}".format(p=pkg_info, r=retval, s=res["Sbuild-Status"]))
        res.add_file(buildlog)
        build_changes_file = os.path.join(path,
                                          "{s}_{v}_{a}.changes".
                                          format(s=self._br["Source"], v=self._br["Version"], a=self._br["Architecture"]))
        if os.path.exists(build_changes_file):
            build_changes = mini_buildd.changes.Changes(build_changes_file)
            build_changes.tar(tar_path=res._file_path + ".tar")
            res.add_file(res._file_path + ".tar")

        res.save()
        res.upload()


class Builder():
    def __init__(self, queue):
        self._queue = queue

    def run(self):
        while True:
            f = self._queue.get()
            mini_buildd.misc.start_thread(Build(f))
            self._queue.task_done()
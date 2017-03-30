# -*- coding: utf-8 -*-

import os
import re
import tempfile
import shutil
import logging

import mini_buildd.misc
import mini_buildd.call
import mini_buildd.setup

LOG = logging.getLogger(__name__)


class Colons(object):
    """
    Provide a colon->name mapping for the gpg script-parsable '--with-colons' output.

    See /usr/share/doc/gnupg/DETAILS.gz.
    """
    def __init__(self, colons_line):
        self._colons = colons_line.split(":")

    def __str__(self):
        return "{t}: {k}: {u}".format(t=self.type, k=self.key_id, u=self.user_id)

    def _get(self, index):
        return mini_buildd.misc.list_get(self._colons, index, "")

    @property
    def type(self):
        return self._get(0)

    @property
    def key_id(self):
        return self._get(4)

    @property
    def creation_date(self):
        return self._get(5)

    @property
    def expiration_date(self):
        return self._get(6)

    @property
    def user_id(self):
        "fingerprint for 'fpr' type"
        return self._get(9)


class BaseGnuPG(object):
    @classmethod
    def get_flavor(cls):
        """
        Ugly-parse GPG binary flavor(=major.minor) "1.4"
        ("classic"), "2.0" ("stable") or "2.1" ("modern") from
        "gpg --version" output (like "gpg (GnuPG)
        2.1.14"). Don't fail but return "unknown" if anything
        nasty happens.
        """
        try:
            version_info = mini_buildd.call.Call(["gpg", "--version"]).check().ustdout.splitlines()
            version_line = version_info[0].split(" ")
            return version_line[2][0:3]
        except BaseException as e:  # pylint: disable=broad-except
            LOG.warning("Can't parse GPG flavor: {e}".format(e=e))
            return "unkown"

    def __init__(self, home):
        self.flavor = self.get_flavor()
        self.home = home
        self.gpg_cmd = ["gpg",
                        "--homedir", home,
                        "--display-charset", mini_buildd.setup.CHAR_ENCODING,
                        "--batch"]
        LOG.info("GPG {f}: {c}".format(f=self.flavor, c=self.gpg_cmd))

    def gen_secret_key(self, template):
        flavor_additions = {"2.1": "\n%no-protection\n"}

        with tempfile.TemporaryFile() as t:
            t.write(template.encode(mini_buildd.setup.CHAR_ENCODING))
            t.write(flavor_additions.get(self.flavor, "").encode(mini_buildd.setup.CHAR_ENCODING))
            t.seek(0)
            mini_buildd.call.Call(self.gpg_cmd + ["--gen-key"], stdin=t).log().check()

    def export(self, dest_file, identity=""):
        with open(dest_file, "w") as f:
            mini_buildd.call.Call(self.gpg_cmd + ["--export"] + ([identity] if identity else []), stdout=f).check()

    def get_pub_key(self, identity):
        return mini_buildd.call.Call(self.gpg_cmd + ["--armor", "--export", identity]).log().check().ustdout

    def _get_colons(self, list_arg="--list-public-keys", type_regex=".*"):
        for line in mini_buildd.call.Call(self.gpg_cmd + [list_arg, "--with-colons", "--fixed-list-mode", "--with-fingerprint", "--with-fingerprint"]).log().check().ustdout.splitlines():
            colons = Colons(line)
            LOG.debug("{c}".format(c=colons))
            if re.match(type_regex, colons.type):
                yield colons

    def get_pub_colons(self, type_regex="pub"):
        return self._get_colons(list_arg="--list-public-keys", type_regex=type_regex)

    def get_sec_colons(self, type_regex="sec"):
        return self._get_colons(list_arg="--list-secret-keys", type_regex=type_regex)

    def get_first_sec_colon(self, type_regex):
        try:
            return next(self.get_sec_colons(type_regex=type_regex))
        except StopIteration:
            return Colons("")

    def get_first_sec_key(self):
        return self.get_first_sec_colon("sec").key_id

    def get_first_sec_key_fingerprint(self):
        return self.get_first_sec_colon("fpr").user_id

    def get_first_sec_key_user_id(self):
        return self.get_first_sec_colon("uid").user_id

    def recv_key(self, keyserver, identity):
        return mini_buildd.call.Call(self.gpg_cmd + ["--armor", "--keyserver", keyserver, "--recv-keys", identity]).log().check().ustdout

    def add_pub_key(self, key):
        with tempfile.TemporaryFile() as t:
            t.write(key.encode(mini_buildd.setup.CHAR_ENCODING))
            t.seek(0)
            mini_buildd.call.Call(self.gpg_cmd + ["--import"], stdin=t).log().check()

    def add_keyring(self, keyring):
        if os.path.exists(keyring):
            self.gpg_cmd += ["--keyring", keyring]
        else:
            LOG.warning("Skipping non-existing keyring file: {k}".format(k=keyring))

    def verify(self, signature, data=None):
        try:
            mini_buildd.call.Call(self.gpg_cmd + ["--verify", signature] + ([data] if data else [])).check()
        except:
            raise Exception("GnuPG authorization failed.")

    def sign(self, file_name, identity=None):
        # 1st: copy the unsigned file and add an extra new line
        # (Like 'debsign' from devscripts does: dpkg-source <= squeeze will have problems without the newline)
        unsigned_file = file_name + ".asc"
        shutil.copyfile(file_name, unsigned_file)
        with open(unsigned_file, "a") as unsigned:
            unsigned.write("\n")

        # 2nd: Sign the file copy
        signed_file = file_name + ".signed"

        def failed_cleanup():
            if os.path.exists(signed_file):
                os.remove(signed_file)

        # Retrying sign call; workaround for mystery https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=849551
        mini_buildd.call.call_with_retry(self.gpg_cmd +
                                         ["--armor", "--textmode", "--clearsign", "--output", signed_file] +
                                         (["--local-user", identity] if identity else []) + [unsigned_file],
                                         retry_max_tries=5,
                                         retry_sleep=1,
                                         retry_failed_cleanup=failed_cleanup)

        # 3rd: Success, move to orig file and cleanup
        os.rename(signed_file, file_name)
        os.remove(unsigned_file)


class GnuPG(BaseGnuPG):
    def __init__(self, template, fullname, email):
        super(GnuPG, self).__init__(home=os.path.join(mini_buildd.setup.HOME_DIR, ".gnupg"))
        self.template = """\
{t}
Name-Real: {n}
Name-Email: {e}
""".format(t=template, n=fullname, e=email)

    def prepare(self):
        if not self.get_pub_key():
            LOG.info("Generating GnuPG secret key (this might take some time)...")
            self.gen_secret_key(self.template)
            LOG.info("New GnuPG secret key prepared...")
        else:
            LOG.info("GnuPG key already prepared...")

    def remove(self):
        if os.path.exists(self.home):
            shutil.rmtree(self.home)
            LOG.info("GnuPG setup removed: {h}".format(h=self.home))

    def get_pub_key(self, identity=None):
        return super(GnuPG, self).get_pub_key("mini-buildd")


class TmpGnuPG(BaseGnuPG, mini_buildd.misc.TmpDir):
    """
    >>> # mini_buildd.setup.DEBUG.append("keep")  # Enable 'keep' for debugging only
    >>> gnupg_home = mini_buildd.misc.TmpDir()
    >>> dummy = shutil.copy2("../examples/doctests/gpg/secring.gpg", gnupg_home.tmpdir)
    >>> dummy = shutil.copy2("../examples/doctests/gpg/pubring.gpg", gnupg_home.tmpdir)
    >>> gnupg = BaseGnuPG(home=gnupg_home.tmpdir)

    >>> gnupg.get_first_sec_colon("sec").type
    'sec'
    >>> gnupg.get_first_sec_key_user_id()
    '\\xdcdo \\xdcmlaut <test@key.org>'
    >>> gnupg.get_first_sec_key()  #doctest: +ELLIPSIS
    'AF95FC80FC40A82E'
    >>> gnupg.get_first_sec_key_fingerprint()  #doctest: +ELLIPSIS
    '4FB13BDD777C046D72D4E7D3AF95FC80FC40A82E'

    >>> export = tempfile.NamedTemporaryFile()
    >>> gnupg.export(export.name)

    >>> t = tempfile.NamedTemporaryFile()
    >>> t.write(b"A test file\\n")
    12
    >>> t.flush()
    >>> gnupg.sign(file_name=t.name, identity="test@key.org")
    >>> gnupg.verify(t.name)
    >>> pub_key = gnupg.get_pub_key(identity="test@key.org")
    >>> tgnupg = TmpGnuPG()
    >>> tgnupg.add_pub_key(pub_key)
    >>> tgnupg.verify(t.name)

    >>> tgnupg.close()
    >>> gnupg_home.close()
    """
    def __init__(self):
        mini_buildd.misc.TmpDir.__init__(self)
        super(TmpGnuPG, self).__init__(home=self.tmpdir)

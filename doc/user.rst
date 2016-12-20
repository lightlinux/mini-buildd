#############
User's Manual
#############

The user's manual covers **using** a mini-buildd installation
-- i.e., everything you can do with it given someone else had
already set it up for you as a service.

************
Introduction
************

The core functionalities of mini-buildd are, 1st the
arch-multiplexed clean building, and 2nd providing a
repository. You don't need to worry about 1st, mini-buildd just
does it for you.

The 2nd however, the repository, goes public and hits "global
Debian namespace"; so, as a big picture, it's important first to
understand how mini-buildd's (default) setup tries to deal with
this.

First of all, each **instance** has it's own **identity**
string, which will be used in the name of the keyring package,
and will also appear in the apt repository in the ``Origin``
field.

Second, each instance instance may have ``N`` **repositories**,
which each have their own **identity** string, determining the
actual distribution names (``CODENAME-ID-SUITE``) to be used for
uploads or in apt lines.

Both identities should be "globally unique" to avoid any
confusion or conflicts with other existing repositories.

.. note:: Exceptions are the generic *Sandbox* and *Developer*
          repositories, with the de-facto standard names
          ``test`` and ``debdev``; these should never be used
          publicly or for anything but testing.

Third, when people are mixing repositories together, we want to avoid
package clashes, like same PACKAGE-VERSION from two different
repositories. Also, we want guaranteed upgradeability between two
different base distributions, and from experimental to
non-experimental suites. Hence, at least in the **Default
Layout**, we also have a **version restriction**, which
resembles that of Debian Backports:

.. _user_default_layouts:

Default Layout
==============

==================== ========= =================== ========================= ========================= ============================ =======================
  The Default Layout's Suites and Semantics Overview
-----------------------------------------------------------------------------------------------------------------------------------------------------------
Suite                Flags     Version restriction Example(``test/jessie``)  Repository                Semantic                     Consumer
==================== ========= =================== ========================= ========================= ============================ =======================
*experimental*       U E 6R    ``~<R><C>+0``       ``~test80+0``             No auto                   *Use at will*                Developer.
snapshot             U E 12R   ``~<R><C>+0``       ``~test80+0``             No auto, but upgrades     *Continuous integration*     Developer, beta tester.
``unstable``         U M 9R    ``~<R><C>+[1-9]``   ``~test80+3``             No auto, but upgrades     *Proposed for live*          Developer, beta tester.
``testing``          M 3R      ``~<R><C>+[1-9]``   ``~test80+2``             No auto, but upgrades     *QA testing*                 Quality Assurance.
``hotfix``           U M 4R    ``~<R><C>+[1-9]``   ``~test80+2+hotfix1``     No auto, but upgrades     *Hotfix proposed for live*   Quality Assurance.
``stable``           6R        ``~<R><C>+[1-9]``   ``~test80+1``             No auto, but upgrades     *Live*                       End customer.
==================== ========= =================== ========================= ========================= ============================ =======================

``U``: Uploadable ``M``: Migrates ``E``: Experimental ``NR``: keeps N Rollback versions ``<R>``: Repository Identity ``<C>``: Codename version.

.. note:: The ``hotfix`` suite fills the kludge in a situation
					when a new version is in ``unstable/testing`` (but no
					yet ready for ``stable``), but you need but to do
					important bug fixes to what is in ``stable``
					immediately (it does migrate to ``stable`` directly).

.. _user_setup:

**********
User Setup
**********

As a **minimal setup**, you should have a *web browser installed*;
you can instantly `browse mini-buildd </mini_buildd/>`_, and use
all functionality that do not require extra permissions.

To be able **use advanced functionality** (for example, create
package subscriptions, access restricted API calls, or upload
your GnuPG public key), create a *user account*:

#. `Register a user account </accounts/register/>`_.
#. `Setup your profile </mini_buildd/accounts/profile/>`_ (package subscriptions, GnuPG key upload).

To **access mini-buildd from the command line** via
``mini-buildd-tool``, install ``python-mini-buildd``::

	# apt-get install python-mini-buildd

To **upload packages**, install ``dput`` and add `mini-buildd's
dput config </mini_buildd/api?command=getdputconf>`_ to your
``~/.dput.cf``::

	# apt-get install dput
	? mini-buildd-tool HOST getdputconf >>~/.dput.cf

.. note:: After ``~/.dput.cf`` has been set up this way, you can
          use ``[USER@]ID``-like shortcuts instead of ``HOST``,
          and these will also appear in the bash auto-completion
          of ``mini-buildd-tool``.


.. _user_repository:

********************
Using the repository
********************

.. _user_upload:

****************
Upload a package
****************

Upload Options
==============

.. versionadded:: 1.0.26

An `Upload Option` is some value induced to mini-buildd via
special entries in the ``changelog`` of an upload. Thus, an upload
may overwrite some defaults, or request special handling.

For example, consider an upload with this ``debian/changelog``::

	mini-buildd (1.0.25~test80+1) jessie-test-unstable; urgency=medium

	  * Adds this.
	  * Adds that.
	  * Fixes something else.
	  * MINI_BUILDD_OPTION: ignore-lintian=true
	  * MINI_BUILDD_OPTION: run-lintian[armel]=false
	  * MINI_BUILDD_OPTION: auto-ports=wheezy-test-unstable

This would

* ignore lintian errors for this upload,
* not run lintian at all for builds on arch ``armel``
* and finally (after successful install) do an automated port to ``wheezy``.

Changelog entries denoting such an ``upload option`` need to be of the form::

	* MINI_BUILDD_OPTION: <key>[[<alt>]]=<value>

For options that support alternate values, values without an ``<alt>`` denote the default for that option.

These ``Upload Options`` are known:

========================= ===================== ========== =============================================================
  Upload Options
------------------------------------------------------------------------------------------------------------------------
Key                       Alt                   Value      Description
========================= ===================== ========== =============================================================
**ignore-lintian**        [``arch``]            Bool       Ignore lintian failures (install anyway).
**run-lintian**           [``arch``]            Bool       Run lintian on build [#run-lintian-note]_.
**internal-apt-priority**                       Int        APT priorities for internal repos on build [#internal-apt-priority-note]_.
**auto-ports**                                  CSV        List of distributions (comma-separated) to automatically run ports for after successful install.
========================= ===================== ========== =============================================================

.. [#run-lintian-note] You cannot currently enable lintian run when it's disabled in the resp. Distribution. So for the time being, only "false" makes sense here.
.. [#internal-apt-priority-note] This will happily override the default (1) or the value of ``Distribution``'s extra option ``Internal-APT-Priority``.

Changelog Magic Lines (deprecated)
----------------------------------

.. deprecated:: 1.0.26
	 Please use `upload options` ``auto-ports`` (for ``AUTO_BACKPORTS``) or ``ignore-lintian`` (for ``BACKPORT_MODE``) instead.

``mini-buildd`` currently supports these so called ``magic
lines`` as changelog entry to control it on a per-upload basis::

	MINI_BUILDD: BACKPORT_MODE
	  Make QA-Checks that usually break when backporting unlethal (like lintian).

	MINI_BUILDD: AUTO_BACKPORTS: CODENAME-REPOID-SUITE[,CODENAME-REPOID-SUITE...]
	  After successful build for the upload distribution, create and upload automatic internal ports for the given distributions.

FAQ
===
.. todo:: **BUG**: *reprepro fails with debian/ as symlink in Debian native packages*

	 Please follow [#debbug768046]_ for this subject.

	 In such a case, builds will be fine, but reprepro will not be
	 able to install the package; you will only be able to see
	 reprepro's error "No section and no priority for" in the
	 ``daemon.log``.

	 For the moment, just avoid such a setup (which is imho not
	 desireable anyway). However, as it's a legal setup afaik it
	 should work after all.

.. _user_api:

*************
Using the API
*************

The ``API`` consists of several commands with optional arguments
(authentity and authority protected via django's user management).

On the web interface, you can see a list of all commands via the `API menu </mini_buildd/api>`_.

There are several ways to access the API:

Via the Web Interface
=====================

API calls are integrated in the web interface at appropriate
places. Credentials are handled by whatever your browser uses.

Chances are that this is all you need, and ``no extra packages``
need to be installed on your system.

Via the Command Line
====================

This needs extra package ``python-mini-buildd`` for the command
line tool ``mini-buildd-tool``. Credentials are handled via
``python-keyring``.

Via Python Code
===============

This needs extra package ``python-mini-buildd`` for the client
API python module ``client_1_0``. Credentials are handled via
``python-keyring``.

Over the mere API calls, this also currently adds some extra
functionality (like *bulk migration*, or *blocking until package
availability*).

For example, one can have configuration-like little python
helper scripts, like for bulk migrating a package::

	#!/usr/bin/python
	from mini_buildd.api.client_1_0 import Daemon
	Daemon("myhost.some.where").login("myuser").bulk_migrate(["mypkg1", "mypkg2"], ["myrepoid"], ["jessie"], ["unstable", "testing"])

You might find some more information in the API doc `here
</doc/mini_buildd.api.html>`_, or directly in the source code.

Access via https proxy
----------------------

If you happen to have setup an https proxy for your mini-buildd
instance (see examples), the above example could be written as::

	#!/usr/bin/python
	from mini_buildd.api.client_1_0 import Daemon
	Daemon("myhost.some.where", port=443, proto="https").login("myuser").bulk_migrate(["mypkg1", "mypkg2"], ["myrepoid"], ["jessie"], ["unstable", "testing"])

In case you use a self-signed certificate, you will also need to make this known
for python's ``urllib2``, for example like so on a Debian system::

	# apt-get install ca-certificates
	# cp your_self_signed_cert.crt /usr/local/share/ca-certificates/
	# update-ca-certificates


.. _user_ports:

***************
Automatic ports
***************

Internal ports
==============

External ports
==============

.. _user_maintenance:

**********************
Repository maintenance
**********************
.. todo:: **IDEA**: *Dependency check on package migration.*

.. todo:: **IDEA**: *Custom hooks (prebuild.d source.changes, preinstall.d/arch.changes, postinstall.d/arch.changes).*

FAQ
===
.. todo:: **FAQ**: *aptitude GUI does not show distribution or origin of packages*

	 To show the **distribution** of packages, just add ``%t`` to
	 the package display format [#debbug484011]_. For example, I
	 do prefer this setting for the *Package-Display-Format*::

		 aptitude::UI::Package-Display-Format "%c%a%M%S %p %t %i %Z %v# %V#";

	 The origin cannot be shown in the package display format
	 [#debbug248561]_. However, you may change the grouping to
	 categorize with "origin". For example, I do prefer this
	 setting for the *Default-Grouping*::

		 aptitude::UI::Default-Grouping "task,status,pattern(~S~i~O, ?true ||),pattern(~S~i~A, ?true ||),section(subdirs,passthrough),section(topdir)";

	 This will group installed packages into an *Origin->Archive*
	 hierarchy.

	 Additionally to aptitude's default "Obsolete and locally
	 installed" top level category (which only shows packages not
	 in any apt archive), this grouping also more conveniently
	 shows installed package _versions_ which are not currently in
	 any repository (check "Installed Packages/now").

.. todo:: **BUG**: *apt secure problems after initial (unauthorized) install of the archive-key package*

	 - aptitude always shows <NULL> archive

	 You can verify this problem via::

		 # aptitude -v show YOURID-archive-keyring | grep ^Archive
		 Archive: <NULL>, now

	 - BADSIG when verifying the archive keyring package's signature

	 Both might be variants of [#debbug657561]_ (known to occur
	 for <= squeeze). For both, check if this::

		 # rm -rf /var/lib/apt/lists/*
		 # apt-get update

	 fixes it.

.. todo:: **FAQ**: *Multiple versions of packages in one distribution*

	 This is not really a problem, but a uncommon situation that
	 may lead to confusion.

	 Generally, reprepro does allow exactly only one version of a
	 package in a distribution; the only exception is when
	 installed in *different components* (e.g., main
	 vs. non-free).

	 This usually happens when the 'Section' changes in the
	 corresponding 'debian/control' file of the source package, or
	 if packages were installed manually using "-C" with reprepro.

	 Check with the "show" command if this is the case, i.e., s.th. like::

		 $ mini-buildd-tool show my-package

	 you may see multiple entries for one distribution with different components.

	 mini-buildd handles this gracefully; the ``remove``,
	 ``migrate`` and ``port`` API calls all include an optional
	 'version' parameter to be able to select a specific version.

	 In the automated rollback handling, all versions of a source
	 package are shifted.


**********
References
**********

.. rubric:: References:
.. [#debbug484011] http://bugs.debian.org/cgi-bin/bugreport.cgi?bug=484011
.. [#debbug248561] http://bugs.debian.org/cgi-bin/bugreport.cgi?bug=248561
.. [#debbug657561] http://bugs.debian.org/cgi-bin/bugreport.cgi?bug=657561
.. [#debbug768046] http://bugs.debian.org/cgi-bin/bugreport.cgi?bug=768046

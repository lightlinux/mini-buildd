###################
Quickstart Tutorial
###################

This shows how to quickly set up a working "test" repository on
a **freshly installed** mini-buildd package, using local chroots
only, plus a very rough roundtrip on the basic usage.

***********
Admin Setup
***********

Enter the web application's `configuration section </admin/mini_buildd/>`_.

1. **Configure** and **prepare** the daemon instance.

   * *Note*: Actions like *[Un]prepare*, *[de]activate*, etc. can be called from a model's list view.
   * *Note*: Daemon prepare will generate your instance ID, read GnuPG key; you may need to generate some entropy on the system if this stalls.

2. **Add** archive(s).
3. **Add** apt key(s) and **activate**.
4. **Add** source(s) and **activate**.
5. **Add** distribution(s).
6. **Add** a repository with identity "test" and **activate**.
7. **Add** chroot(s) and **activate**.

   * *Note*: Preparing chroots (hence the http request) take a while -- *stay tuned!*

8. **Activate** the daemon.
9. **Generate** keyring packages for your test repository in the `repository list view </admin/mini_buildd/repository>`_.
10. **Control status** on `mini-buildd's home </mini_buildd/>`_.
11. **Search** packages using "*" as pattern to see all.
12. **Propagate** the new keyring package to \*-testing and \*-stable.


User uploads
============

* Use mini-buildd's `Dput config </mini_buildd/download/dput.cf>`_ to upload packages.
* Upload authorization works via GnuPG signing. To enable user uploads:

   * You may disable auth completely for for the repository.
   * You may add a django user, and configure an Uploader object for him.
   * You may add predefined GnuPG keyrings to the repository.

Using the repository
====================

- Go to the `test repository overview </mini_buildd/repositories/test>`_, and grab the needed apt lines.
- Install the keyring package on your system.
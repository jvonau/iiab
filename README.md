*Sharing the World's Free Knowledge*

# Internet-in-a-Box (IIAB)

[Internet-in-a-Box (IIAB)](https://internet-in-a-box.org) is a “learning hotspot” that brings the Internet's crown jewels
(Wikipedia in any language, thousands of Khan Academy videos, zoomable OpenStreetMap, electronic books, WordPress journaling, “Toys from Trash” electronics projects, ETC) to those without Internet.

You can build your own tiny, affordable server (an offline digital library) for your school, your medical clinic, your prison, your region and/or your very own family — accessible with any nearby smartphone, tablet or laptop.

Internet-in-a-Box gives you the DIY tools to:
1. Download then drag-and-drop to arrange the [very best of the World’s Free Knowledge](https://internet-in-a-box.org/#quality-content).
2. Choose among [30+ powerful educational apps](https://wiki.iiab.io/go/FAQ#What_services_%28IIAB_apps%29_are_suggested_during_installation%3F) for your school or learning/teaching community, optionally with a complete LMS (learning management system).
3. Exchange local/indigenous knowledge with nearby communities, using our [Manage Content](https://github.com/iiab/iiab-admin-console/blob/master/roles/console/files/help/InstContent.rst#manage-content) interface and possible mesh networking.

FYI this [community product](https://en.wikipedia.org/wiki/Internet-in-a-Box) is enabled by professional volunteers working [side-by-side](https://wiki.iiab.io/go/FAQ#What_are_the_best_places_for_community_support%3F) with schools, clinics and libraries around the world.  *Thank you for being a part of our http://OFF.NETWORK grassroots technology [movement](https://meta.wikimedia.org/wiki/Internet-in-a-Box)!*

## Installation

Install Internet-in-a-Box (IIAB) from: [**download.iiab.io**](https://download.iiab.io/)

Please see [FAQ.IIAB.IO](https://wiki.iiab.io/go/FAQ) which has 50+ questions and answers to help you along the way (e.g. [“Is a quick installation possible?”](https://wiki.iiab.io/go/FAQ#Is_a_quick_installation_possible%3F)) as you put together the <!--digital--> “local learning hotspot” most suitable for your own teaching/learning community.  Here are 2 ways to install IIAB:

- Our [1-line installer](https://download.iiab.io/) gets you the very latest, typically within about an hour, on [different Linux distributions](https://github.com/iiab/iiab/wiki/IIAB-Platforms#operating-systems).
- [Prefab disk images](https://github.com/iiab/iiab/wiki/Raspberry-Pi-Images-~-Summary#iiab-images-for-raspberry-pi) ([image files](https://github.com/iiab/iiab/releases)) can be flashed directly onto a microSD card, for insertion into Raspberry Pi.

Our [HOW-TO videos](https://www.youtube.com/channel/UC0cBGCxr_WPBPa3IqPVEe3g) can be very helpful and the [Installation](https://github.com/iiab/iiab/wiki/IIAB-Installation) wiki page has more intricate details e.g. if you're trying to install Internet-in-a-Box (IIAB) onto a [another Linux](https://github.com/iiab/iiab/wiki/IIAB-Platforms) that has not yet been tried.

See our [Tech Docs Wiki](https://github.com/iiab/iiab/wiki) for more about the underlying nuts and bolts.

After you've installed the software, you should [add content](https://github.com/iiab/iiab/wiki/IIAB-Installation#add-content), which can of course take time when downloading multi-gigabyte Content Packs!

Finally, you can [customize your Internet-in-a-Box home page](https://wiki.iiab.io/go/FAQ#How_do_I_customize_my_Internet-in-a-Box_home_page%3F) (typically http://box or http://box.lan) using our **drag-and-drop** Admin Console (http://box.lan/admin) &mdash; to arrange Content Packs and IIAB Apps (services) for your local community's needs.

## Running roles individually

`./runrole` applies one role using `/etc/iiab/local_vars.yml`. Runs are
repeatable and use local files when possible: the role is installed if missing,
repaired offline if possible, and otherwise only reconfigured. `--upgrade` is
the explicit network/version refresh.

    sudo ./runrole captiveportal                    # apply
    sudo ./runrole --add captiveportal              # apply despite captiveportal_install: False
    sudo ./runrole --reinstall captiveportal        # reinstall the program
    sudo ./runrole --upgrade zimantic               # upgrade, where supported
    sudo ./runrole --clean-install zimantic       # also reset dependencies, caches, and settings
    sudo ./runrole --uninstall zimantic             # uninstall, where supported
    ./runrole --status captiveportal                # show installed/desired state
    sudo ./runrole --check captiveportal            # best-effort dry run

The repair options escalate, from least to most work:

- `--reinstall` rebuilds the program only. Every role accepts it.
- `--upgrade` upgrades it where supported.
- `--clean-install` also resets dependencies, caches, and settings where
  supported.
- `--uninstall` removes the program where supported.

`--reinstall`, `--upgrade`, and `--clean-install` act on a role that the
configuration says should be installed, so they also need `*_install: True`;
pass `--add` to act on a role whose `*_install` is `False`. `--uninstall` is
the exception, since it removes the installation and then asks you to set
`*_install: False`.

`./runroles` accepts the same options and multiple roles, e.g.
`./runroles --status captiveportal kiwix`.

`--reinstall` and `--clean-install` remove the role's entries from
`/etc/iiab/iiab_state.yml` before running. Most roles skip `install.yml` while
their `*_installed` variable is defined, so this forces a reinstall. `runrole`
exits if the role has no entry; `runroles` installs such roles normally.
`--uninstall` removes the same entries and asks the role to clean up after
itself.

A role supports `--uninstall`, `--upgrade`, or `--clean-install` when its
`tasks/main.yml` or `tasks/main.yaml` implements that operation. `runrole` and
`runroles` detect this from the role entrypoint, so an unused
`tasks/uninstall.yml` file alone does not opt a role in and no separate
metadata file is needed. For example, a role can reference a feature task file
from its entrypoint, or gate work with `when: uninstall`:

    - name: Uninstall Example
      include_tasks: uninstall.yml
      when: uninstall

Operations that are not detected are refused: using one of those flags on a
role that does not implement it prints an error and exits nonzero before
anything is changed. `--status` reports the capabilities detected for a role.
`--reinstall` remains available to every role for compatibility.

For unusual roles whose implementation cannot be detected from their tasks, an
optional `roles/<role>/meta/runrole.yml` can still opt in:

    ---
    uninstall: true
    upgrade: true
    force_reinstall: true

### Role task lifecycle

Roles that adopt the safe-apply pattern use a small, fixed set of task files so
a maintainer can decide where a change belongs without guessing. `tasks/main.yml`
only validates and dispatches; it should not contain the work itself. Zimantic
is the reference implementation; other roles can adopt the layout incrementally.

| File | When it runs | May use the network? |
|---|---|---|
| `validate.yml` | every apply except `--uninstall` | no |
| `uninstall.yml` | `--uninstall` | no |
| `reinstall.yml` | `--reinstall` and `--force-reinstall` | no |
| `update.yml` | first install, version change, `--upgrade`, `--force-reinstall`, a missing checkout, or a checkout that does not match `zimantic_version` | yes |
| `install.yml` | every apply; skips work when already usable | only when update ran, the venv is new and wheels are in use, or a build prerequisite is missing |
| `configure.yml` | every apply | only to fetch a missing immutable asset |
| `state.yml` | after a successful apply | no |

The rules are intentionally simple:

- `update.yml` is the only phase allowed to fetch a new program version or
  refresh package metadata.
- `install.yml` must be safe to run offline when the source checkout and
  virtual environment already exist. It repairs a missing executable with
  `--no-deps` and never deletes data or downloads. When distribution packages
  provide the native dependencies, it always uses `--no-deps` so pip cannot
  replace them.
- `configure.yml` reconciles desired state on every run. It may fetch a missing
  model or other checksummed asset, but never deletes or re-downloads one that
  is already present.
- `reinstall.yml` only stops the service and, for `--force-reinstall`, removes
  the directories that are safe to rebuild. Heavy downloads stay in
  `update.yml` or `configure.yml`.
- `install.yml` writes the installed/version marker as soon as the build
  succeeds. `state.yml` only reports the desired settings in `iiab.ini`.
- Put computed values in `vars/main.yml`, user-facing knobs in
  `defaults/main.yml`, and restarts in `handlers/main.yml`. Reserve `set_fact`
  for runtime state that must survive later tasks.
- `*_installed` and `*_downloaded` markers only exist after a role has written
  them to `iiab_state.yml`. Never test one directly. For a boolean check, use
  the `installed` lookup (`lookup_plugins/installed.py`), which returns false
  when the marker is undefined:

      when: lookup('installed', 'mysql')

  Keep `when: mysql_installed is undefined` for the install gate. The lifecycle
  test reports a bare marker check as a failure.

`scripts/test-role-lifecycle.py` scans these task files and prints `WARN` lines
with the file, line, and task name whenever a phase does network work that
belongs in `update.yml` — for example a `get_url` in `install.yml`, a package
install in `main.yml`, or an `include_tasks: update.yml` from `uninstall.yml`.
`main.yml` including `update.yml`, `uninstall.yml`, or `reinstall.yml` without a
`when:` guard is also reported. `configure.yml` may still fetch a missing
immutable asset; program checkouts and dependency installs there are reported.
The warnings do not fail the test, and are expected to be noisy while roles
adopt the layout incrementally.

The scanner cannot evaluate Ansible variables or `when:` conditions. A task
that must use the network in an offline phase can carry a
`# lifecycle: allow-network <reason>` comment immediately above it (or on its
first line). Use it only for the documented exceptions, and say why.

`--add` (`-a`) overrides `*_install: False` for that invocation only and does
not change `local_vars.yml`. Normal runs do not prompt; `--uninstall` and
`--clean-install` ask for `yes` unless `-y` is given (and only when there is
an existing installation to remove or reset). `--check` runs Ansible in check
mode (`--check --diff`), which not every role supports. `--status` is read-only
and cannot be combined with the other options.

## Community

Global community updates and videos are regularly posted to: **[@internet_in_box](https://twitter.com/internet_in_box)**

_Internet-in-a-Box (IIAB) greatly welcomes contributions from educators, librarians and [IT/UX/QA people](https://github.com/iiab/iiab/wiki/Contributors-Guide-(EN)) ([versión en español](https://github.com/iiab/iiab/wiki/Gu%C3%ADa-para-Contribuidores-(ES))) of all kinds!_

If you would like to volunteer, please [make contact](https://internet-in-a-box.org/contributing.html) after looking over [“How can I help?”](https://wiki.iiab.io/go/FAQ#How_can_I_help%3F) at: [FAQ.IIAB.IO](https://wiki.iiab.io/go/FAQ)

<!-- To learn about our software architecture, check out our [Contributors Guide](https://github.com/iiab/iiab/wiki/IIAB-Contributors-Guide).-->

To learn more about our open community architecture for “offline” learning, check out [“What technical documentation exists?”](https://wiki.iiab.io/go/FAQ#What_technical_documentation_exists%3F)
FYI we use [Ansible](https://wiki.iiab.io/go/FAQ#What_is_Ansible_and_what_version_should_I_use%3F) <!--as the underlying technology--> to install, deploy, configure and manage the various software components.

*Thank you for helping us enable offline access to the Internet's free/open knowledge jewels, as well as “Sneakernet-of-Alexandria” distribution of local/indigenous content, when mass media channels do not serve grassroots voices.*

## Versions

Pre-releases of Internet-in-a-Box (IIAB) undergo continuous QA / continuous integration / continuous deployment and are **strongly recommended!**

Install our latest pre-release using the 1-line installer at: [**download.iiab.io**](https://download.iiab.io/)

You can also consider <!--latest Internet-in-a-Box (IIAB)--> earlier official releases at: [github.com/iiab/iiab/releases](https://github.com/iiab/iiab/releases)

For much older versions, see: [github.com/xsce](https://github.com/xsce)

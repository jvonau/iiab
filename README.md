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
repeatable: the role is installed if missing, upgraded if needed, and otherwise
only reconfigured.

    sudo ./runrole captiveportal                    # apply
    sudo ./runrole --add captiveportal              # apply despite captiveportal_install: False
    sudo ./runrole --reinstall captiveportal        # reinstall the program
    sudo ./runrole --upgrade captiveportal          # upgrade, where supported
    sudo ./runrole --force-reinstall captiveportal  # also reset dependencies, caches, and settings
    sudo ./runrole --uninstall captiveportal        # uninstall, where supported
    sudo ./runrole --status captiveportal           # show installed/desired state
    sudo ./runrole --check captiveportal            # best-effort dry run

The repair options escalate, from least to most work:

- `--reinstall` rebuilds the program only. Every role accepts it.
- `--upgrade` upgrades it where supported.
- `--force-reinstall` also resets dependencies, caches, and settings where
  supported.
- `--uninstall` removes the program where supported.

`./runroles` accepts the same options and multiple roles, e.g.
`sudo ./runroles --status captiveportal kiwix`.

`--reinstall` and `--force-reinstall` remove the role's entries from
`/etc/iiab/iiab_state.yml` before running. Most roles skip `install.yml` while
their `*_installed` variable is defined, so this forces a reinstall. `runrole`
exits if the role has no entry; `runroles` installs such roles normally.
`--uninstall` removes the same entries and asks the role to clean up after
itself.

A role supports `--uninstall`, `--upgrade`, or `--force-reinstall` when its
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

`--add` (`-a`) overrides `*_install: False` for that invocation only and does
not change `local_vars.yml`. Normal runs do not prompt; `--uninstall` and
`--force-reinstall` ask for `yes` unless `-y` is given. `--check` runs Ansible in
check mode (`--check --diff`), which not every role supports.

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

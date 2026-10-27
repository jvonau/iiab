#!/usr/bin/env python3
"""Run role lifecycle scenarios against a virtual Ansible environment."""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
ACTIVE_STATES = {"started", "restarted", "reloaded"}
BASELINE_SERVICES = {"nginx", "dnsmasq"}
MOCK_MODULE_SOURCE = r'''#!/usr/bin/python3
import ast
import copy
import json
import os
import posixpath
import re
import shlex
import sys


def module_name():
    name = os.path.basename(sys.argv[0])
    return name[len("AnsiballZ_") :] if name.startswith("AnsiballZ_") else name


def decode(value):
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value


def module_args():
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        with open(sys.argv[1], encoding="utf-8") as args_file:
            text = args_file.read()
        if text.startswith("{"):
            return json.loads(text).get("ANSIBLE_MODULE_ARGS", {})
        raw_match = re.search(
            r"_raw_params=(.*?)(?=\s+[A-Za-z_][A-Za-z0-9_]*=|\s+_ansible_|$)",
            text,
        )
        args = {}
        if raw_match:
            args["_raw_params"] = decode(raw_match.group(1))
            text = text[raw_match.end() :]
        for key, value in re.findall(
            r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)", text
        ):
            args[key] = decode(value)
        return args
    return {}


def load_state():
    state_file = os.environ["IIAB_MOCK_STATE"]
    if os.path.isfile(state_file):
        with open(state_file, encoding="utf-8") as stream:
            return json.load(stream)
    return {"paths": {}, "services": {}}


def save_state(state):
    with open(os.environ["IIAB_MOCK_STATE"], "w", encoding="utf-8") as stream:
        json.dump(state, stream)


def finish(state, **result):
    save_state(state)
    print(json.dumps(result))
    raise SystemExit(0)


def fail(state, message):
    finish(state, failed=True, changed=False, msg=message)


def exists(state, path):
    prefix = path.rstrip("/") + "/"
    return path in state["paths"] or any(
        item.startswith(prefix) for item in state["paths"]
    )


def unit_path(service):
    return (
        f"/etc/systemd/system/{service}"
        if service.endswith(".service")
        else f"/etc/systemd/system/{service}.service"
    )


def create(state, path, kind="file"):
    changed = not exists(state, path)
    state["paths"][path] = kind
    return changed


def remove(state, path):
    prefix = path.rstrip("/") + "/"
    paths = [
        item
        for item in state["paths"]
        if item == path or item.startswith(prefix)
    ]
    for item in paths:
        del state["paths"][item]
    return bool(paths)


def record_service(state, name, enabled=None, service_state=None):
    service = state["services"].setdefault(
        name, {"enabled": False, "state": "stopped"}
    )
    if enabled is not None:
        service["enabled"] = enabled
    if service_state:
        service["state"] = service_state
    state.setdefault("events", []).append(
        {
            "phase": os.environ.get("IIAB_MOCK_PHASE"),
            "name": name,
            "enabled": enabled,
            "state": service_state,
        }
    )


def command_result(state, args):
    command = args.get("_raw_params") or args.get("cmd") or ""
    creates = args.get("creates")
    if creates and exists(state, creates):
        finish(state, changed=False, skipped=True, rc=0, stdout="", stderr="")
    if creates:
        create(state, creates)

    if "systemctl" in command or re.search(r"(^|\s)service\s", command):
        words = shlex.split(command)
        actions = {
            "start": "started",
            "restart": "restarted",
            "reload": "reloaded",
            "stop": "stopped",
        }
        for index, word in enumerate(words):
            if word in actions and index + 1 < len(words):
                record_service(
                    state,
                    words[index + 1],
                    service_state=actions[word],
                )
            elif word == "enable" and index + 1 < len(words):
                record_service(state, words[index + 1], enabled=True)
            elif word == "disable" and index + 1 < len(words):
                record_service(state, words[index + 1], enabled=False)
        finish(state, changed=False, rc=0, stdout="", stderr="")

    if "dpkg-query" in command:
        stdout = "installed"
    elif "rev-parse" in command:
        stdout = "mock-commit"
    elif "describe --tags" in command:
        stdout = "v0.1.5"
    elif "remote get-url" in command:
        stdout = "https://github.com/chapmanjacobd/zimantic.git"
    elif "--version" in command:
        stdout = "Python 3.14.0"
    elif "df " in command:
        stdout = "1000"
    elif "ls /sys/class/net |" in command:
        stdout = "1"
    elif "is-active" in command:
        stdout = "active"
    elif "pip install" in command:
        stdout = "Successfully installed zimantic"
    else:
        stdout = ""
    finish(state, changed=False, rc=0, stdout=stdout, stderr="")


def main():
    name = module_name()
    args = module_args()
    state = load_state()

    if name == "stat":
        path = args.get("path", "")
        installed = os.environ.get("IIAB_MOCK_INSTALLED") == "true"
        finish(
            state,
            changed=False,
            stat={
                "exists": exists(state, path) or installed,
                "isdir": state["paths"].get(path) == "directory",
            },
        )

    if name in {
        "file",
        "template",
        "copy",
        "get_curl",
        "lineinfile",
        "ini_file",
    }:
        path = args.get("path") or args.get("dest")
        if not path:
            fail(state, f"{name} mock needs a path or destination")
        if name in {"template", "copy"} and path.endswith("/"):
            path = posixpath.join(path, posixpath.basename(args.get("src", "")))
        if args.get("state") == "absent":
            changed = remove(state, path)
        else:
            kind = "directory" if args.get("state") == "directory" else "file"
            changed = create(state, path, kind)
            if name == "template" and path.startswith("/etc/systemd/system/"):
                for service in state.get("expected_services", []):
                    changed = create(state, unit_path(service)) or changed
        finish(state, changed=changed, path=path)

    if name in {"group", "user"}:
        finish(state, changed=False)

    if name == "git":
        destination = args.get("dest")
        create(state, destination, "directory")
        create(state, posixpath.join(destination, ".git"), "directory")
        finish(state, changed=True, after="mock-commit")

    if name in {
        "apt",
        "dnf",
        "yum",
        "package",
        "unarchive",
        "get_url",
        "mount",
        "sysctl",
        "uri",
        "mariadb_db",
        "mariadb_user",
        "postgresql_db",
        "postgresql_user",
    }:
        finish(state, changed=False)

    if name in {"command", "shell"}:
        command_result(state, args)

    if name in {"systemd", "systemd_service", "service"}:
        if not args.get("name"):
            finish(state, changed=False)
        service = args["name"]
        installed = os.environ.get("IIAB_MOCK_INSTALLED") == "true"
        unit = unit_path(service)
        available = service in state.get("available_services", [])
        if not installed and service not in state["services"] and not exists(state, unit):
            if not available:
                fail(state, f"service {service} is not installed")
        record_service(
            state,
            service,
            enabled=args.get("enabled"),
            service_state=args.get("state"),
        )
        finish(state, changed=False)

    if name == "mock_checkpoint":
        snapshot = args.get("snapshot")
        state.setdefault("snapshots", {})[snapshot] = {
            "paths": copy.deepcopy(state["paths"]),
            "services": copy.deepcopy(state["services"]),
        }
        finish(state, changed=False)

    if name in {"raw", "script"}:
        fail(state, f"{name} execution is disabled in lifecycle tests")

    finish(state, changed=False)


if __name__ == "__main__":
    main()
'''

MOCK_ACTION_SOURCE = r'''from __future__ import annotations

import json
import os
import posixpath

from ansible.plugins.action import ActionBase


def unit_path(service):
    return (
        f"/etc/systemd/system/{service}"
        if service.endswith(".service")
        else f"/etc/systemd/system/{service}.service"
    )


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        args = self._task.args
        path = args.get("dest") or args.get("path")
        if not path:
            result.update(failed=True, msg="mock action needs a destination")
            return result

        with open(os.environ["IIAB_MOCK_STATE"], encoding="utf-8") as stream:
            state = json.load(stream)
        if path.endswith("/"):
            path = posixpath.join(path, posixpath.basename(args.get("src", "")))
        changed = path not in state["paths"]
        state["paths"][path] = "file"
        if path.startswith("/etc/systemd/system/"):
            for service in state.get("expected_services", []):
                unit = unit_path(service)
                if unit not in state["paths"]:
                    state["paths"][unit] = "file"
                    changed = True
        with open(os.environ["IIAB_MOCK_STATE"], "w", encoding="utf-8") as stream:
            json.dump(state, stream)
        result.update(changed=changed, path=path)
        return result
'''


def role_var(role: str) -> str:
    return {
        "calibre-web": "calibreweb",
        "iiab-admin": "iiab_admin",
        "osm-vector-maps": "osm_vector_maps",
    }.get(role, role)


def role_supports(role: str, feature: str) -> bool:
    manifest = ROOT_DIR / "roles" / role / "meta" / "runrole.yml"
    if manifest.is_file():
        if re.search(
            rf"^{re.escape(feature)}:\s*(true|yes|on)\s*$",
            manifest.read_text(encoding="utf-8"),
            re.IGNORECASE | re.MULTILINE,
        ):
            return True

    tasks_dir = ROOT_DIR / "roles" / role / "tasks"
    entrypoint = next(
        (tasks_dir / name for name in ("main.yml", "main.yaml") if (tasks_dir / name).is_file()),
        None,
    )
    if entrypoint is None:
        return False
    text = entrypoint.read_text(encoding="utf-8")
    feature_pattern = re.escape(feature)
    if re.search(
        rf"^\s*(-\s+)?(?:include_tasks|import_tasks):\s*['\"]?"
        rf"(?:[^'\"]*/)?{feature_pattern}(?:\.ya?ml)?['\"]?\s*$",
        text,
        re.MULTILINE,
    ):
        return True
    for line in text.splitlines():
        if "when:" in line and not re.search(
            rf"not\s+{feature_pattern}(?:[^A-Za-z0-9_-]|$)", line
        ) and re.search(rf"(?<![A-Za-z0-9_-]){feature_pattern}(?![A-Za-z0-9_-])", line):
            return True
        if re.match(rf"^\s*-\s+{feature_pattern}\s*(#.*)?$", line):
            return True
        if re.match(rf"^\s*{feature_pattern}(?:\s+(?:or|and))?\s*(#.*)?$", line):
            return True
    return False


def enabled_var(role: str) -> str:
    tasks_dir = ROOT_DIR / "roles" / role / "tasks"
    for path in sorted(tasks_dir.rglob("*")):
        if path.is_file():
            matches = re.findall(r"[A-Za-z0-9_]+_enabled", path.read_text(encoding="utf-8"))
            if matches:
                return matches[0]
    return f"{role_var(role)}_enabled"


def role_service_names(role: str) -> set[str]:
    services = set()
    tasks_dir = ROOT_DIR / "roles" / role / "tasks"
    for path in sorted(tasks_dir.rglob("*")):
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            module = re.match(r"^\s+(?:systemd_service|systemd|service):\s*$", line)
            if not module:
                continue
            module_indent = len(line) - len(line.lstrip())
            for following in lines[index + 1 : index + 8]:
                if following.strip() and len(following) - len(following.lstrip()) <= module_indent:
                    break
                match = re.match(
                    r'^\s+name:\s*["\']?([A-Za-z0-9@_.-]+)["\']?\s*$',
                    following,
                )
                if match:
                    services.add(match.group(1).removesuffix(".service"))
                    break
    return services


def initial_state(role: str, installed: bool = False) -> dict:
    state = {
        "paths": {},
        "services": {"nginx": {"enabled": True, "state": "started"}},
        "expected_services": sorted(role_service_names(role)),
        "available_services": [
            "nginx",
            "dnsmasq",
            "apache2",
            "avahi-daemon",
            "hostapd",
            "hostapd.service",
            "iiab-clone-wifi.service",
            "mariadb",
            "php8.2-fpm",
            "postgresql-iiab",
            "ssh",
            "systemd-resolved",
            "networking",
            "wpa_supplicant",
            "uwsgi-app@captiveportal.socket",
            "uwsgi-app@captiveportal.service",
        ],
    }
    state["paths"].update(
        {
            "/lib/systemd/system/uwsgi-app@.service": "file",
            "/lib/systemd/system/uwsgi-app@.socket": "file",
        }
    )
    if role == "firmware":
        state["available_services"].append("iiab-check-firmware.service")
    if installed:
        for service in role_service_names(role):
            if service in state["services"] or service in state["available_services"]:
                continue
            unit = (
                f"/etc/systemd/system/{service}"
                if service.endswith(".service")
                else f"/etc/systemd/system/{service}.service"
            )
            state["paths"][unit] = "file"
            state["services"][service] = {"enabled": True, "state": "started"}
    return state


def role_manages_service_toggle(role: str) -> bool:
    tasks_dir = ROOT_DIR / "roles" / role / "tasks"
    return any(
        path.is_file() and "enable-or-disable" in path.stem
        for path in tasks_dir.rglob("*")
    )


def role_toggle_service_names(role: str) -> set[str]:
    services = set()
    tasks_dir = ROOT_DIR / "roles" / role / "tasks"
    for path in tasks_dir.rglob("*"):
        if not path.is_file() or "enable-or-disable" not in path.stem:
            continue
        services.update(role_service_names_from_files((path,)))
    return services


def role_service_names_from_files(paths) -> set[str]:
    services = set()
    for path in paths:
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            module = re.match(r"^\s+(?:systemd_service|systemd|service):\s*$", line)
            if not module:
                continue
            module_indent = len(line) - len(line.lstrip())
            for following in lines[index + 1 : index + 8]:
                if following.strip() and len(following) - len(following.lstrip()) <= module_indent:
                    break
                match = re.match(
                    r'^\s+name:\s*["\']?([A-Za-z0-9@_.-]+)["\']?\s*$',
                    following,
                )
                if match:
                    services.add(match.group(1).removesuffix(".service"))
                    break
    return services


def prepare_controller_files(scenario: Path) -> None:
    template_roles = scenario / "templates" / "roles"
    template_roles.parent.mkdir(parents=True, exist_ok=True)
    template_roles.symlink_to(ROOT_DIR / "roles", target_is_directory=True)
    (scenario / "roles").symlink_to(ROOT_DIR / "roles", target_is_directory=True)


def role_tasks(role: str, scenario: Path, installed: bool) -> str:
    if role != "zimantic":
        return ""
    return textwrap.dedent(
        f"""\
            - set_fact:
                kiwix_enabled: false
                zimantic_use_system_packages: never
                zimantic_source_dir: "{scenario}/source"
                zimantic_venv: "{scenario}/source/.venv"
                zimantic_data_dir: "{scenario}/data"
                zimantic_config_file: "{scenario}/data/config.toml"
                zimantic_index_dir: "{scenario}/data/indexes"
                zimantic_model_dir: "{scenario}/data/model"
                zimantic_zim_dir: "{scenario}/zims/content"
                nginx_conf_dir: "{scenario}/nginx"
                iiab_base: "{scenario}/iiab"
                content_base: "{scenario}/library"
                iiab_zim_path: "{scenario}/zims"
                iiab_ini_file: "{scenario}/iiab.ini"
                iiab_state_file: "{scenario}/iiab_state.yml"
                zimantic_installed: {str(installed).lower()}
                zimantic_installed_version: v0.1.5
        """
    ).rstrip()


def playbook_header(
    role: str,
    vars_file: str,
    install_name: str,
    enabled_name: str,
    enabled: bool,
    installed: bool,
    scenario: Path,
    phase: str | None = None,
    lifecycle: dict | None = None,
) -> str:
    lifecycle = lifecycle or {}
    vars_path = vars_file or str(ROOT_DIR / "vars" / "default_vars.yml")
    lines = [
        "- hosts: localhost",
        "  connection: local",
        "  gather_facts: false",
        "  vars_files:",
        f'    - "{vars_path}"',
        "  vars:",
        f'    ansible_python_interpreter: "{sys.executable}"',
        "    ansible_facts:",
        "      machine: x86_64",
        "      distribution: Debian",
        '      distribution_major_version: "13"',
        '      distribution_version: "13.0"',
        "      pkg_mgr: apt",
        "      default_ipv4:",
        "        alias: eth0",
        "        gateway: 192.0.2.1",
        "      ansible_local:",
        "        local_facts:",
        '          systemd_networkd: "disabled"',
        '          network_manager: "disabled"',
        '          dhcpcd: "disabled"',
        "    is_proot: false",
        "    iiab_stage: 8",
        "    no_net_restart: false",
        '    iiab_wireless_lan_iface: "wlan0"',
        '    iiab_lan_iface: "eth0"',
        '    iiab_wan_iface: "eth0"',
        '    rpi_model: "none"',
        '    os_ver: "debian-13"',
        "    is_debian: true",
        "    is_raspbian: false",
        "    is_ubuntu: false",
        '    dpkg_arch: {"stdout": "amd64"}',
        '    php_version: "8.2"',
        "    skip_role_on_error: false",
        f'    uninstall: {str(lifecycle.get("uninstall", False)).lower()}',
        f'    reinstall: {str(lifecycle.get("reinstall", False)).lower()}',
        f'    force_reinstall: {str(lifecycle.get("force_reinstall", False)).lower()}',
        f'    upgrade: {str(lifecycle.get("upgrade", False)).lower()}',
        f"    {install_name}: true",
        f"    {enabled_name}: {str(enabled).lower()}",
        "  environment:",
        f'    IIAB_MOCK_STATE: "{scenario}/state.json"',
        f'    IIAB_MOCK_INSTALLED: "{str(installed).lower()}"',
    ]
    if phase:
        lines.append(f'    IIAB_MOCK_PHASE: "{phase}"')
    lines.append("  tasks:")
    return "\n".join(lines) + "\n"


def make_playbook(
    role: str,
    vars_file: str,
    install_name: str,
    enabled_name: str,
    scenario: Path,
    *,
    enabled: bool,
    installed: bool,
    lifecycle: dict | None = None,
    phase: str | None = None,
    checkpoint: str | None = None,
    seed_installed: bool = False,
) -> str:
    content = playbook_header(
        role,
        vars_file,
        install_name,
        enabled_name,
        enabled,
        installed,
        scenario,
        phase,
        lifecycle,
    )
    if phase:
        lifecycle = lifecycle or {}
        content += textwrap.indent(
            textwrap.dedent(
                f"""\
                    - set_fact:
                        {install_name}: true
                        {enabled_name}: {str(enabled).lower()}
                        uninstall: {str(lifecycle.get("uninstall", False)).lower()}
                        reinstall: {str(lifecycle.get("reinstall", False)).lower()}
                        force_reinstall: {str(lifecycle.get("force_reinstall", False)).lower()}
                        upgrade: {str(lifecycle.get("upgrade", False)).lower()}
                """
            ),
            "    ",
        )
    if seed_installed:
        content += textwrap.indent(
            textwrap.dedent(
                f"""\
                    - set_fact:
                        {role_var(role)}_installed: true
                """
            ),
            "    ",
        )
    role_specific = role_tasks(role, scenario, installed)
    if role_specific:
        content += textwrap.indent(role_specific + "\n", "    ")
    content += textwrap.indent(
        textwrap.dedent(
            f"""\
                - include_role:
                    name: {role}
            """
        ),
        "    ",
    )
    if checkpoint:
        content += textwrap.indent(
            textwrap.dedent(
                f"""\
                    - name: Record {checkpoint} virtual state
                      mock_checkpoint:
                        snapshot: {checkpoint}
                """
            ),
            "    ",
        )
    return content


def create_mock_library(directory: Path) -> Path:
    library = directory / "library"
    library.mkdir()
    module = directory / "mock-module.py"
    module.write_text(MOCK_MODULE_SOURCE, encoding="utf-8")
    module.chmod(0o755)
    actions = directory / "actions"
    actions.mkdir()
    for name in ("copy", "template"):
        action = actions / f"{name}.py"
        action.write_text(MOCK_ACTION_SOURCE, encoding="utf-8")
    names = {
        "stat",
        "file",
        "template",
        "copy",
        "get_curl",
        "lineinfile",
        "ini_file",
        "group",
        "user",
        "git",
        "apt",
        "package",
        "dnf",
        "yum",
        "unarchive",
        "get_url",
        "uri",
        "mount",
        "sysctl",
        "command",
        "shell",
        "systemd",
        "systemd_service",
        "service",
        "raw",
        "script",
        "setup",
        "mock_checkpoint",
    }
    for name in names:
        (library / name).symlink_to(module)
    collection_modules = {
        ("ansible", "mariadb"): {"mariadb_db", "mariadb_user"},
        ("community", "general"): {"htpasswd", "npm"},
        ("community", "postgresql"): {"postgresql_db", "postgresql_user"},
    }
    for (namespace, collection), module_names in collection_modules.items():
        module_dir = (
            directory
            / "collections"
            / "ansible_collections"
            / namespace
            / collection
            / "plugins"
            / "modules"
        )
        module_dir.mkdir(parents=True)
        for name in module_names:
            (module_dir / name).symlink_to(module)
    return library


def run_ansible(
    playbook: Path,
    library: Path,
    state_file: Path,
    installed: bool | None = None,
    phase: str | None = None,
    extra_vars: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "ANSIBLE_CONFIG": str(ROOT_DIR / "ansible.cfg"),
            "ANSIBLE_ROLES_PATH": str(ROOT_DIR / "roles"),
            "ANSIBLE_LIBRARY": str(library),
            "ANSIBLE_COLLECTIONS_PATH": str(library.parent / "collections"),
            "ANSIBLE_ACTION_PLUGINS": str(library.parent / "actions"),
            "ANSIBLE_NOCOLOR": "1",
            "IIAB_MOCK_STATE": str(state_file),
        }
    )
    if installed is not None:
        environment["IIAB_MOCK_INSTALLED"] = str(installed).lower()
    if phase:
        environment["IIAB_MOCK_PHASE"] = phase
    command = [
        shutil.which("ansible-playbook") or "ansible-playbook",
        "--forks=1",
        "-i",
        "localhost,",
        str(playbook),
    ]
    if extra_vars:
        command.extend(["--extra-vars", json.dumps(extra_vars)])
    return subprocess.run(
        command,
        cwd=ROOT_DIR,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def operation_result(
    name: str,
    role: str,
    vars_file: str,
    library: Path,
    temp_dir: Path,
    enabled_name: str,
    install_name: str,
    values: dict,
) -> str:
    scenario = temp_dir / name
    scenario.mkdir()
    prepare_controller_files(scenario)
    (scenario / "iiab_state.yml").touch()
    state_file = scenario / "state.json"
    state_file.write_text(
        json.dumps(initial_state(role, values["installed"])), encoding="utf-8"
    )
    playbook = scenario / "playbook.yml"
    playbook.write_text(
        make_playbook(
            role,
            vars_file,
            install_name,
            enabled_name,
            scenario,
            enabled=values["enabled"],
            installed=values["installed"],
            lifecycle=values,
        ),
        encoding="utf-8",
    )
    extra_vars = {
        install_name: True,
        enabled_name: values["enabled"],
        **{key: values[key] for key in ("uninstall", "reinstall", "force_reinstall", "upgrade")},
    }
    if role == "zimantic":
        extra_vars["zimantic_installed"] = values["installed"]
    result = run_ansible(
        playbook,
        library,
        state_file,
        installed=values["installed"],
        phase=name,
        extra_vars=extra_vars,
    )
    output = [f"  {name:<16}"]
    if result.returncode:
        output.append("FAIL\n")
        output.append("".join(result.stdout.splitlines(True)[-40:]))
        output.append("".join(result.stderr.splitlines(True)[-40:]))
        return "".join(output)
    if role == "zimantic":
        state = json.loads(state_file.read_text(encoding="utf-8"))
        unit = "/etc/systemd/system/zimantic.service"
        service = state["services"].get("zimantic")
        if name == "uninstall":
            ok = unit not in state["paths"] and service and service["state"] == "stopped"
        elif name == "disabled-apply":
            ok = unit not in state["paths"] and service is None
        else:
            ok = unit in state["paths"] and service and service["state"] in ACTIVE_STATES
        if not ok:
            output.append("FAIL\nvirtual state assertion failed\n")
            output.append(state_file.read_text(encoding="utf-8"))
            return "".join(output)
    output.append("PASS\n")
    return "".join(output)


def toggle_result(
    role: str,
    vars_file: str,
    library: Path,
    temp_dir: Path,
    enabled_name: str,
    install_name: str,
    uninstall_supported: bool,
    toggle_supported: bool,
    toggle_services: set[str],
) -> str:
    scenario = temp_dir / "toggle"
    if not toggle_supported:
        return "  toggle          SKIP (role has no service toggle tasks)\n"
    scenario.mkdir()
    prepare_controller_files(scenario)
    (scenario / "iiab_state.yml").touch()
    state_file = scenario / "state.json"
    state_file.write_text(json.dumps(initial_state(role)), encoding="utf-8")
    playbook = scenario / "playbook.yml"
    plays = []
    phases = [
        ("enabled", True, False),
        ("disabled", False, True),
        ("enabled-again", True, True),
    ]
    if uninstall_supported:
        phases.append(("uninstall", True, True))
    for phase, enabled, installed in phases:
        plays.append(
            make_playbook(
                role,
                vars_file,
                install_name,
                enabled_name,
                scenario,
                enabled=enabled,
                installed=installed,
                lifecycle={"uninstall": phase == "uninstall"},
                phase=phase,
                checkpoint=phase,
                seed_installed=installed,
            )
        )
    playbook.write_text("---\n" + "".join(plays), encoding="utf-8")
    result = run_ansible(playbook, library, state_file)
    output = [f"  {'toggle':<16}"]
    if result.returncode:
        output.append("FAIL\n")
        output.append("".join(result.stdout.splitlines(True)[-40:]))
        output.append("".join(result.stderr.splitlines(True)[-40:]))
        return "".join(output)

    state = json.loads(state_file.read_text(encoding="utf-8"))
    snapshots = state.get("snapshots", {})
    required = {"enabled", "disabled", "enabled-again"}
    if uninstall_supported:
        required.add("uninstall")
    if not required <= snapshots.keys():
        output.append("FAIL\nvirtual state did not record all toggle phases\n")
        output.append(f"recorded snapshots: {sorted(snapshots)}\n")
        return "".join(output)

    events = state.get("events", [])
    active = {
        event["name"]
        for event in events
        if event["phase"] == "enabled" and event["state"] in ACTIVE_STATES
    }
    stopped = {
        event["name"]
        for event in events
        if event["phase"] == "disabled" and event["state"] == "stopped"
    }
    active_again = {
        event["name"]
        for event in events
        if event["phase"] == "enabled-again" and event["state"] in ACTIVE_STATES
    }
    managed = active & toggle_services - BASELINE_SERVICES
    if not managed:
        output.append("SKIP (role created no systemd service)\n")
        return "".join(output)
    if missing := managed - stopped:
        output.append(f"FAIL\nservices not stopped when disabled: {sorted(missing)}\n")
        return "".join(output)
    if missing := managed - active_again:
        output.append(f"FAIL\nservices not started when re-enabled: {sorted(missing)}\n")
        return "".join(output)

    for name in managed:
        enabled_service = snapshots["enabled"]["services"][name]
        disabled_service = snapshots["disabled"]["services"][name]
        enabled_again_service = snapshots["enabled-again"]["services"][name]
        if enabled_service["state"] not in ACTIVE_STATES:
            output.append(f"FAIL\n{name} was not active after enabling\n")
            return "".join(output)
        if disabled_service["state"] != "stopped":
            output.append(f"FAIL\n{name} was not stopped after disabling\n")
            return "".join(output)
        if enabled_again_service["state"] not in ACTIVE_STATES:
            output.append(f"FAIL\n{name} was not active after re-enabling\n")
            return "".join(output)
        if uninstall_supported:
            uninstall_service = snapshots["uninstall"]["services"].get(name)
            if not uninstall_service or uninstall_service["state"] != "stopped":
                output.append(f"FAIL\n{name} was not stopped during uninstall\n")
                return "".join(output)
    output.append("PASS\n")
    return "".join(output)


def available_roles() -> list[str]:
    roles = []
    for role_dir in sorted((ROOT_DIR / "roles").iterdir()):
        if (
            not role_dir.is_dir()
            or role_dir.name == "0-DEPRECATED-ROLES"
            or re.match(r"^\d-", role_dir.name)
        ):
            continue
        if any((role_dir / "tasks" / name).is_file() for name in ("main.yml", "main.yaml")):
            roles.append(role_dir.name)
    return roles


def run_role(role: str, vars_file: str, quiet: bool = False) -> int:
    role_dir = ROOT_DIR / "roles" / role
    if not re.fullmatch(r"[A-Za-z0-9_-]+", role):
        print(f"ERROR: invalid role name: {role}", file=sys.stderr)
        return 1
    if not any((role_dir / "tasks" / name).is_file() for name in ("main.yml", "main.yaml")):
        print(f"ERROR: role not found or has no tasks/main.yml: {role}", file=sys.stderr)
        return 1
    if vars_file and not Path(vars_file).is_file():
        print(f"ERROR: vars file not found: {vars_file}", file=sys.stderr)
        return 1

    enabled_name = enabled_var(role)
    install_name = f"{role_var(role)}_install"
    operations: list[tuple[str, dict]] = [
        ("apply", {"installed": False, "enabled": True}),
        ("disabled-apply", {"installed": False, "enabled": False}),
        ("toggle", {}),
    ]
    skipped: list[str] = []
    uninstall_supported = role_supports(role, "uninstall")
    toggle_supported = role_manages_service_toggle(role)
    toggle_services = role_toggle_service_names(role)
    if role_supports(role, "force_reinstall"):
        operations.extend(
            [
                ("reinstall", {"installed": True, "enabled": True, "reinstall": True}),
                (
                    "clean-install",
                    {"installed": True, "enabled": True, "reinstall": True, "force_reinstall": True},
                ),
            ]
        )
    else:
        skipped.extend(["reinstall", "clean-install"])
    if uninstall_supported:
        operations.append(("uninstall", {"installed": True, "enabled": False, "uninstall": True}))
    else:
        skipped.append("uninstall")
    if role_supports(role, "upgrade"):
        operations.append(("upgrade", {"installed": True, "enabled": True, "upgrade": True}))
    else:
        skipped.append("upgrade")

    if not quiet:
        print(f"Testing {role} lifecycle with mocked filesystem, services, and processes...")
        for name in skipped:
            print(f"  {name:<16} SKIP (unsupported)")

    with tempfile.TemporaryDirectory(prefix="iiab-role-lifecycle.") as directory:
        temp_dir = Path(directory)
        library = create_mock_library(temp_dir)
        futures = {}
        with ThreadPoolExecutor(max_workers=len(operations)) as executor:
            for name, values in operations:
                if name == "toggle":
                    future = executor.submit(
                        toggle_result,
                        role,
                        vars_file,
                        library,
                        temp_dir,
                        enabled_name,
                        install_name,
                        uninstall_supported,
                        toggle_supported,
                        toggle_services,
                    )
                else:
                    for lifecycle_flag in (
                        "uninstall",
                        "reinstall",
                        "force_reinstall",
                        "upgrade",
                    ):
                        values.setdefault(lifecycle_flag, False)
                    future = executor.submit(
                        operation_result,
                        name,
                        role,
                        vars_file,
                        library,
                        temp_dir,
                        enabled_name,
                        install_name,
                        values,
                    )
                futures[name] = future
            failures = 0
            for name, _ in operations:
                output = futures[name].result()
                if not quiet or "FAIL" in output:
                    print(output, end="")
                if " FAIL\n" in output or output.endswith("FAIL\n"):
                    failures = 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", nargs="?")
    parser.add_argument("vars_file", nargs="?")
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_roles",
        help="test every active role and print only failures",
    )
    parser.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if shutil.which("ansible-playbook") is None:
        parser.error("ansible-playbook is required")

    if args.all_roles:
        if args.role or args.vars_file:
            parser.error("--all does not accept a role or vars file")
        failures = 0
        for role in available_roles():
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), role, "--quiet"],
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                failures = 1
                print(f"ERROR: {role}", file=sys.stderr)
                if result.stdout:
                    print(result.stdout, end="", file=sys.stderr)
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr)
        return failures
    if not args.role:
        parser.error("a role is required unless --all is used")
    return run_role(args.role, args.vars_file or "", args.quiet)


if __name__ == "__main__":
    sys.exit(main())

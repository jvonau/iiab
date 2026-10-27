#!/usr/bin/env python3
"""Run role lifecycle scenarios against a virtual Ansible environment.

Besides the scenarios, task files are scanned for lifecycle warnings: network
work outside update.yml, and offline phases that include a network phase.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
ACTIVE_STATES = {"started", "restarted", "reloaded"}
BASELINE_SERVICES = {"nginx", "dnsmasq"}

# Task files defined by the "Role task lifecycle" section of README.md. Only
# these need phase-aware checks; helper files are reached through them.
LIFECYCLE_FILES = {
    "main",
    "validate",
    "uninstall",
    "reinstall",
    "update",
    "install",
    "configure",
    "state",
    "enable-or-disable",
}

# update.yml is the only phase allowed to fetch a new program version or
# refresh package metadata.
NETWORK_PHASES = {"update"}
# configure.yml may fetch a missing immutable asset (model, checksummed
# archive), but program and dependency installs belong in update.yml.
ASSET_FETCH_PHASES = {"configure"}

PACKAGE_MODULES = {"apt", "apk", "dnf", "package", "pacman", "yum", "zypper"}
INSTALLER_MODULES = {"composer", "gem", "npm", "pnpm", "pip", "pip3", "snap", "yarn"}
SHELL_MODULES = {"command", "raw", "shell"}
INCLUDE_MODULES = {"include", "include_tasks", "import_tasks"}

LIFECYCLE_DIRECTIVE = re.compile(
    r"#\s*lifecycle:\s*(?P<rule>allow-network)\b", re.IGNORECASE
)

NETWORK_COMMAND = re.compile(
    r"(?:^|[\s;&|(`])(?:"
    r"curl|wget|"
    r"git\s+(?:clone|fetch|ls-remote|pull|submodule\s+update)|"
    r"(?:python[0-9.]*\s+-m\s+)?pip[0-9.]*\s+(?:download|install|wheel)|"
    r"(?:apt|apt-get|aptitude)\s+(?:build-dep|dist-upgrade|install|reinstall|update|upgrade)|"
    r"(?:dnf|yum)\s+(?:install|makecache|update|upgrade)|"
    r"zypper\s+(?:install|refresh|update|up)|"
    r"pacman\s+-S|"
    r"(?:npm|pnpm|yarn)\s+(?:add|ci|install|update)|"
    r"(?:gem|snap)\s+(?:install|refresh|update)"
    r")\b",
    re.IGNORECASE,
)

LIFECYCLE_HINTS = {
    "main": "tasks/main.yml should only validate and dispatch; move the network work to update.yml",
    "validate": "validate.yml must not use the network; move the fetch to update.yml",
    "uninstall": "uninstall.yml must work offline; move the fetch to update.yml",
    "reinstall": "reinstall.yml must work offline; move the fetch to update.yml",
    "install": "install.yml must stay offline-safe; move the fetch to update.yml",
    "configure": "configure.yml may only fetch a missing immutable asset; move program and dependency installs to update.yml",
    "state": "state.yml only records settings; move the fetch to update.yml",
    "enable-or-disable": "enable-or-disable.yml only toggles services; move the fetch to update.yml",
}

# Phases that main.yml must only include conditionally.
GATED_PHASES = {
    "uninstall": "uninstall.yml must only run when uninstall is set; add a when: guard",
    "reinstall": "reinstall.yml must only run for --reinstall or --force-reinstall; add a when: guard",
    "update": "update.yml only runs on first install, a version change, --upgrade, or --force-reinstall; add a when: guard",
}

TASK_KEYWORDS = {
    "__line__",
    "action",
    "always",
    "any_errors_fatal",
    "args",
    "async",
    "become",
    "become_exe",
    "become_flags",
    "become_method",
    "become_user",
    "block",
    "changed_when",
    "check_mode",
    "collections",
    "connection",
    "debugger",
    "delay",
    "delegate_facts",
    "delegate_to",
    "diff",
    "environment",
    "failed_when",
    "ignore_errors",
    "ignore_unreachable",
    "listen",
    "local_action",
    "loop",
    "loop_control",
    "module_defaults",
    "name",
    "no_log",
    "notify",
    "poll",
    "port",
    "register",
    "remote_user",
    "rescue",
    "retries",
    "run_once",
    "tags",
    "throttle",
    "timeout",
    "until",
    "vars",
    "when",
    "with_dict",
    "with_fileglob",
    "with_first_found",
    "with_flattened",
    "with_items",
    "with_nested",
    "with_sequence",
    "with_subelements",
    "with_together",
}
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
        # Ansible writes some argument files as shell-like key=value text
        # (e.g. command with cmd/creates), where values may be quoted and
        # contain spaces. Shlex keeps those values intact; the previous regex
        # stopped at the first space and truncated commands such as pip install.
        try:
            tokens = shlex.split(text)
        except ValueError:
            tokens = text.split()
        args = {}
        for token in tokens:
            key, separator, value = token.partition("=")
            if separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
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
        finish(state, changed=changed, path=path, dest=path)

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
        "mariadb_db",
        "mariadb_user",
        "postgresql_db",
        "postgresql_user",
    }:
        finish(state, changed=False)

    if name in {"command", "shell"}:
        command_result(state, args)

    if name == "uri":
        status = 302 if str(args.get("method", "GET")).upper() == "POST" else 200
        finish(
            state,
            changed=False,
            status=status,
            url=args.get("url", ""),
            attempts=1,
            cookies={"MATOMO_SESSID": "mock"},
        )

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

MOCK_PAUSE_ACTION_SOURCE = r'''from __future__ import annotations

from ansible.plugins.action import ActionBase


class ActionModule(ActionBase):
    """Return immediately instead of sleeping (roles pause for emphasis)."""

    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        result.update(
            changed=False,
            delta=0,
            echo=True,
            rc=0,
            start="",
            stop="",
            stdout="Paused for 0.0 minutes",
            user_input="",
        )
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
            "transmission-daemon",    # provided by the transmission-daemon package
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


class _LifecycleLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader, node):
    # Keep the source line so warnings can point at the exact task.
    data = yaml.SafeLoader.construct_mapping(loader, node)
    data["__line__"] = node.start_mark.line + 1
    return data


_LifecycleLoader.add_constructor(
    _LifecycleLoader.DEFAULT_MAPPING_TAG, _construct_mapping
)


def load_task_file(path: Path, text: str | None = None):
    if text is None:
        text = path.read_text(encoding="utf-8")
    return yaml.load(text, Loader=_LifecycleLoader)


def lifecycle_directives(lines: list[str], line: int | None) -> set[str]:
    """Collect ``# lifecycle: ...`` directives for a task.

    A directive may be on the task's first line or in the comment block
    directly above it. Blank lines end the block.
    """
    if line is None or not 1 <= line <= len(lines):
        return set()
    candidates = [lines[line - 1]]
    index = line - 2
    while index >= 0 and lines[index].lstrip().startswith("#"):
        candidates.append(lines[index])
        index -= 1
    return {
        match.group("rule").lower()
        for candidate in candidates
        for match in LIFECYCLE_DIRECTIVE.finditer(candidate)
    }


def task_arg(args, name: str, default=None):
    if isinstance(args, dict):
        return args.get(name, default)
    if isinstance(args, str):
        match = re.search(
            rf"(?:^|\s){re.escape(name)}=(\"[^\"]*\"|'[^']*'|\S+)", args
        )
        if match:
            return match.group(1).strip("\"'")
    return default


def is_true(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "on", "true", "yes"}


def task_command(task: dict, args) -> str:
    if isinstance(args, str):
        return args
    command = task_arg(args, "cmd") or task_arg(args, "_raw_params")
    task_args = task.get("args")
    if not command and isinstance(task_args, dict):
        command = task_args.get("cmd", "")
    return str(command or "")


def network_action(module: str, args, task: dict) -> tuple[str, str] | None:
    """Return (kind, description) for a task that uses the network.

    ``kind`` lets configure.yml accept fetching an immutable asset while still
    rejecting program checkouts and dependency installs.
    """
    module = module.rsplit(".", 1)[-1]

    if module in {"get_url", "get_curl"}:
        return "asset", "downloads a file"
    if module == "git":
        return "checkout", "fetches a git checkout"
    if module == "unarchive":
        src = str(task_arg(args, "src", ""))
        if is_true(task_arg(args, "remote_src")) or re.match(
            r"^(?:ftp|https?)://", src
        ):
            return "asset", "downloads and extracts an archive"
        return None
    if module == "uri":
        url = task_arg(args, "url", "a remote host")
        if task_arg(args, "dest"):
            return "asset", f"downloads {url}"
        return "asset", f"makes an HTTP request to {url}"
    if module in {"apt_key", "apt_repository"}:
        if str(task_arg(args, "state", "present")).lower() != "absent":
            return "metadata", f"fetches {module} metadata"
        return None
    if module in PACKAGE_MODULES:
        state = str(task_arg(args, "state", "present")).lower()
        if state in {"absent", "removed", "purged"}:
            return None
        name = task_arg(args, "name") or task_arg(args, "pkg")
        if module == "apt" and is_true(task_arg(args, "update_cache")) and not name:
            return "install", "refreshes the apt cache"
        if module == "apt" and task_arg(args, "upgrade"):
            return "install", "upgrades apt packages"
        if module == "package":
            return "install", "installs system packages"
        if state == "latest":
            return "install", f"installs or upgrades {module} packages"
        return "install", f"installs {module} packages"
    if module in INSTALLER_MODULES:
        state = str(task_arg(args, "state", "present")).lower()
        if state not in {"absent", "removed", "purged"}:
            return "install", f"installs {module} packages"
        return None
    if module in SHELL_MODULES:
        command = task_command(task, args)
        match = NETWORK_COMMAND.search(command)
        if match:
            word = match.group(0).strip().split()[0].lower()
            if word in {"curl", "wget"}:
                kind = "asset"
            elif word == "git":
                kind = "checkout"
            else:
                kind = "install"
            return kind, f"runs `{match.group(0).strip()}` (network)"
        return None
    return None


def included_phase(module: str, args) -> str | None:
    if module.rsplit(".", 1)[-1] not in INCLUDE_MODULES:
        return None
    value = task_arg(args, "file") or task_arg(args, "_raw_params")
    if not value and isinstance(args, str):
        value = args
    if not value:
        return None
    stem = Path(str(value)).stem
    return stem if stem in LIFECYCLE_FILES else None


def walk_tasks(items, guarded: bool = False):
    if not isinstance(items, list):
        return
    for task in items:
        if not isinstance(task, dict):
            continue
        task_guarded = guarded or "when" in task
        yield task, task_guarded
        for block_key in ("block", "rescue", "always"):
            yield from walk_tasks(task.get(block_key), task_guarded)


def task_modules(task: dict) -> list[tuple[str, object]]:
    return [
        (key, value)
        for key, value in task.items()
        if key not in TASK_KEYWORDS and not str(key).startswith("with_")
    ]


def format_lifecycle_warning(
    path: Path, line: int | None, task: dict, message: str
) -> str:
    location = str(path.relative_to(ROOT_DIR))
    if line is not None:
        location += f":{line}"
    name = re.sub(r"\s+", " ", str(task.get("name", ""))).strip()
    if len(name) > 90:
        name = name[:87] + "..."
    label = f" [{name}]" if name else ""
    if len(message) > 160:
        message = message[:157] + "..."
    return f"  WARN {location}{label}: {message}\n"


def lifecycle_warnings(role: str) -> list[str]:
    """Warn when a task file does work that belongs in another phase."""
    tasks_dir = ROOT_DIR / "roles" / role / "tasks"
    warnings = []
    paths = sorted(
        set(tasks_dir.glob("*.yml")) | set(tasks_dir.glob("*.yaml"))
    )
    for path in paths:
        phase = path.stem
        if phase not in LIFECYCLE_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tasks = load_task_file(path, text)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            warnings.append(
                f"  WARN {path.relative_to(ROOT_DIR)}: could not read or parse "
                f"task file for lifecycle checks ({error.__class__.__name__})\n"
            )
            continue
        lines = text.splitlines()
        for task, guarded in walk_tasks(tasks):
            line = task.get("__line__")
            if "allow-network" in lifecycle_directives(lines, line):
                # Deliberate exception, explained next to the task.
                continue
            for module, args in task_modules(task):
                network = network_action(module, args, task)
                if network:
                    kind, action = network
                    if phase in NETWORK_PHASES:
                        continue
                    if phase in ASSET_FETCH_PHASES and kind == "asset":
                        continue
                    warnings.append(
                        format_lifecycle_warning(
                            path,
                            line,
                            task,
                            f"{module} {action}; {LIFECYCLE_HINTS[phase]}",
                        )
                    )

                included = included_phase(module, args)
                if not included or included == phase:
                    continue
                # main.yml is supposed to dispatch to these phases; every
                # other offline phase including them can pull in the network.
                if phase == "main":
                    if included in GATED_PHASES and not guarded:
                        warnings.append(
                            format_lifecycle_warning(
                                path,
                                line,
                                task,
                                f"includes {included}.yml unconditionally; "
                                f"{GATED_PHASES[included]}",
                            )
                        )
                    continue
                if phase == "configure":
                    suspicious = {"install", "update"}
                else:
                    suspicious = {"configure", "install", "update"}
                if included in suspicious:
                    warnings.append(
                        format_lifecycle_warning(
                            path,
                            line,
                            task,
                            f"includes {included}.yml, which may use the network; "
                            f"{LIFECYCLE_HINTS[phase]}",
                        )
                    )
    return warnings


def prepare_controller_files(scenario: Path) -> None:
    template_roles = scenario / "templates" / "roles"
    template_roles.parent.mkdir(parents=True, exist_ok=True)
    template_roles.symlink_to(ROOT_DIR / "roles", target_is_directory=True)
    (scenario / "roles").symlink_to(ROOT_DIR / "roles", target_is_directory=True)


def role_tasks(role: str, scenario: Path, installed: bool) -> str:
    if role != "zimantic":
        return ""
    settings = [
        "kiwix_enabled: false",
        "zimantic_use_system_packages: never",
        f'zimantic_source_dir: "{scenario}/source"',
        f'zimantic_venv: "{scenario}/source/.venv"',
        f'zimantic_data_dir: "{scenario}/data"',
        f'zimantic_config_file: "{scenario}/data/config.toml"',
        f'zimantic_index_dir: "{scenario}/data/indexes"',
        f'zimantic_model_dir: "{scenario}/data/model"',
        f'zimantic_zim_dir: "{scenario}/zims/content"',
        f'nginx_conf_dir: "{scenario}/nginx"',
        f'iiab_base: "{scenario}/iiab"',
        f'content_base: "{scenario}/library"',
        f'iiab_zim_path: "{scenario}/zims"',
        f'iiab_ini_file: "{scenario}/iiab.ini"',
        f'iiab_state_file: "{scenario}/iiab_state.yml"',
    ]
    # A real iiab_state.yml has zimantic_installed only after install.yml has
    # written it, so leave the marker undefined on a fresh host.
    if installed:
        settings.append("zimantic_installed: true")
    settings.append("zimantic_installed_version: v0.1.5")
    return "\n".join(["- set_fact:", *(f"    {setting}" for setting in settings)])


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
    for name in ("copy", "template", "unarchive"):
        action = actions / f"{name}.py"
        action.write_text(MOCK_ACTION_SOURCE, encoding="utf-8")
    (actions / "pause.py").write_text(MOCK_PAUSE_ACTION_SOURCE, encoding="utf-8")
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
        "cron",
        "pip",
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
    # default_vars.yml sets skip_role_on_error: True, and vars_files beat the
    # play-level value in make_playbook(). Force it as an extra var so a real
    # role failure makes the lifecycle test fail instead of silently passing.
    scenario_vars = {"skip_role_on_error": False}
    if extra_vars:
        scenario_vars.update(extra_vars)
    command.extend(["--extra-vars", json.dumps(scenario_vars)])

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
    if role == "zimantic" and values["installed"]:
        extra_vars["zimantic_installed"] = True
    result = run_ansible(
        playbook,
        library,
        state_file,
        installed=values["installed"],
        phase=name,
        extra_vars=extra_vars,
    )
    output = [f"  {name:<19}"]
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
            # Fresh apply with _enabled: False (the './runrole --add' case):
            # install the unit, but do not start the service.
            ok = unit in state["paths"] and (
                service is None or service["state"] not in ACTIVE_STATES
            )
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
        return f"  {'toggle':<19}SKIP (role has no service toggle tasks)\n"
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
    output = [f"  {'toggle':<19}"]
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
    # An already-installed role that is disabled is covered by the 'disabled'
    # phase of the toggle scenario below, so it is not run separately.
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
    warnings = lifecycle_warnings(role)
    if warnings:
        print(f"  lifecycle warnings: {len(warnings)}")
        for warning in warnings:
            print(warning, end="")
    if not quiet:
        for name in skipped:
            print(f"  {name:<19} SKIP (unsupported)")

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


def parse_shard(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)/(\d+)", value.strip())
    if not match:
        raise argparse.ArgumentTypeError("expected INDEX/TOTAL, e.g. 1/4")
    index, total = int(match.group(1)), int(match.group(2))
    if total < 1 or not 1 <= index <= total:
        raise argparse.ArgumentTypeError("INDEX must be between 1 and TOTAL")
    return index, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", nargs="?")
    parser.add_argument("vars_file", nargs="?")
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_roles",
        help="test every active role and print lifecycle warnings and failures",
    )
    parser.add_argument(
        "--shard",
        type=parse_shard,
        metavar="INDEX/TOTAL",
        help="with --all, test only one shard of roles (e.g. 1/4) for CI parallelism",
    )
    parser.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if shutil.which("ansible-playbook") is None:
        parser.error("ansible-playbook is required")

    if args.shard and not args.all_roles:
        parser.error("--shard requires --all")

    if args.all_roles:
        if args.role or args.vars_file:
            parser.error("--all does not accept a role or vars file")
        all_roles = available_roles()
        roles = all_roles
        if args.shard:
            index, total = args.shard
            roles = all_roles[index - 1 :: total]
            print(
                f"Shard {index}/{total}: {len(roles)} of {len(all_roles)} roles",
                file=sys.stderr,
            )
        if not roles:
            return 0
        failures = 0
        workers = min(8, len(roles), os.cpu_count() or 1)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                role: executor.submit(
                    subprocess.run,
                    [sys.executable, str(Path(__file__).resolve()), role, "--quiet"],
                    cwd=ROOT_DIR,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                for role in roles
            }
            for role in roles:
                result = futures[role].result()
                if result.returncode:
                    failures = 1
                    print(f"ERROR: {role}", file=sys.stderr)
                    if result.stdout:
                        print(result.stdout, end="", file=sys.stderr)
                    if result.stderr:
                        print(result.stderr, end="", file=sys.stderr)
                elif result.stdout:
                    # Quiet mode only prints lifecycle warnings for a passing role.
                    print(result.stdout, end="")
        return failures
    if not args.role:
        parser.error("a role is required unless --all is used")
    return run_role(args.role, args.vars_file or "", args.quiet)


if __name__ == "__main__":
    sys.exit(main())

#!/bin/bash

# Shared by ./runrole and ./runroles. Both set these paths before sourcing:
# DEFAULT_VARS_FILE, LOCAL_VARS_FILE, IIAB_STATE_FILE, IIAB_INI_FILE.

# Role and variable names sometimes differ (for example, calibre-web and
# calibreweb).
role_var() {
    case $1 in
        calibre-web)
            echo calibreweb
            ;;
        iiab-admin)
            echo iiab_admin
            ;;
        osm-vector-maps)    # TO BE REMOVED in 2026/2027
            echo osm_vector_maps
            ;;
        *)
            echo "$1"
            ;;
    esac
}

role_manifest_file() {
    echo "roles/$1/meta/runrole.yml"
}

role_tasks_dir() {
    echo "roles/$1/tasks"
}

# True if <role> implements <feature> (uninstall, upgrade, or force_reinstall).
#
# No manifest is required. A role is recognized when tasks/main.yml or
# tasks/main.yaml includes the feature task file or references the feature
# variable in a condition such as 'when: upgrade'. A meta/runrole.yml can still
# opt in explicitly, for the rare role whose implementation is not visible
# from its entrypoint.
role_supports() {
    local role=$1
    local feature=$2
    local manifest
    local tasks_dir
    local entrypoint
    manifest=$(role_manifest_file "$role")
    tasks_dir=$(role_tasks_dir "$role")

    # Optional explicit opt-in, for roles whose implementation cannot be
    # recognized from their task files.
    if [ -f "$manifest" ] && grep -Eq "^${feature}:[[:space:]]*([Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Oo][Nn])[[:space:]]*$" "$manifest"; then
        return 0
    fi

    [ -d "$tasks_dir" ] || return 1
    if [ -f "$tasks_dir/main.yml" ]; then
        entrypoint="$tasks_dir/main.yml"
    elif [ -f "$tasks_dir/main.yaml" ]; then
        entrypoint="$tasks_dir/main.yaml"
    else
        return 1
    fi

    # Include/import of a dedicated task file from the role entrypoint.
    if grep -Eq "^[[:space:]]*(-[[:space:]]+)?(include_tasks|import_tasks):[[:space:]]*['\"]?([^'\"]*/)?${feature}(\.ya?ml)?['\"]?[[:space:]]*$" "$entrypoint"; then
        return 0
    fi

    # Single-line conditions such as 'when: uninstall' or
    # 'when: reinstall or force_reinstall'.
    if grep -Eq "^[[:space:]]*when:[^#]*[^[:alnum:]_-]${feature}([^[:alnum:]_-]|$)" "$entrypoint"; then
        return 0
    fi

    # List-style conditions, e.g. a '- uninstall' item below 'when:'.
    if grep -Eq "^[[:space:]]*-[[:space:]]+(not[[:space:]]+)?${feature}[[:space:]]*(#.*)?$" "$entrypoint"; then
        return 0
    fi

    # Multi-line conditions, where the variable sits on a line of its own (for
    # example 'upgrade or' inside a '>-' block).
    if grep -Eq "^[[:space:]]*${feature}([[:space:]]+(or|and))?[[:space:]]*(#.*)?$" "$entrypoint"; then
        return 0
    fi

    return 1
}

# Print an error and return nonzero unless <role> implements <feature>.
role_require_capability() {
    local role=$1
    local flag=$2
    local feature=$3
    if role_supports "$role" "$feature"; then
        return 0
    fi
    echo -e "\e[1mERROR: role '$role' does not support $flag.\e[0m" >&2
    echo "No '$feature' implementation was detected in $(role_tasks_dir "$role")/main.yml." >&2
    echo "Reference tasks/${feature}.yml from the role entrypoint, add a 'when: ${feature}' condition there, or declare '$feature: true' in $(role_manifest_file "$role")." >&2
    return 1
}

STATE_BACKUP_FILE=""

backup_state_file() {
    local backup
    backup=$(mktemp /tmp/iiab-state-backup.XXXXXX) || {
        echo "ERROR: could not create a backup of $IIAB_STATE_FILE." >&2
        return 1
    }
    if ! cp -- "$IIAB_STATE_FILE" "$backup"; then
        rm -f -- "$backup"
        echo "ERROR: could not back up $IIAB_STATE_FILE." >&2
        return 1
    fi
    STATE_BACKUP_FILE="$backup"
}

restore_state_file() {
    if [ -z "$STATE_BACKUP_FILE" ]; then
        return 0
    fi
    if ! cp -- "$STATE_BACKUP_FILE" "$IIAB_STATE_FILE"; then
        echo "ERROR: could not restore $IIAB_STATE_FILE from its backup." >&2
        return 1
    fi
    echo "Restored $IIAB_STATE_FILE after uninstall failure." >&2
}

cleanup_state_backup() {
    if [ -n "$STATE_BACKUP_FILE" ]; then
        rm -f -- "$STATE_BACKUP_FILE"
        STATE_BACKUP_FILE=""
    fi
}

# Ask for explicit confirmation before an operation that removes data. Callers
# set ASSUME_YES from -y/--yes.
confirm_destructive() {
    local ans
    if [ "$ASSUME_YES" = true ]; then
        return 0
    fi
    echo -n "Type 'yes' to continue: "
    if [ -t 0 ]; then
        read -r ans < /dev/tty
    else
        echo
        echo "No TTY: refusing without --yes." >&2
        return 1
    fi
    echo
    [ "$ans" = "yes" ]
}

# True if <role>_install resolves to True across default + local vars.
role_install_enabled() {
    local var
    local enabled=false
    var=$(role_var "$1")
    grep -q "^${var}_install:\s\+[Tt]rue\b" "$DEFAULT_VARS_FILE" && enabled=true
    grep -q "^${var}_install:\s\+[Ff]alse\b" "$LOCAL_VARS_FILE" && enabled=false
    grep -q "^${var}_install:\s\+[Tt]rue\b" "$LOCAL_VARS_FILE" && enabled=true
    $enabled
}

status_role() {
    local role="$1"
    local var
    local feature
    local capabilities=()
    var=$(role_var "$role")
    echo "Role: $role (variable prefix: ${var}_)"
    if [ -f "$IIAB_STATE_FILE" ] && grep -q "^${var}_" "$IIAB_STATE_FILE"; then
        echo "Installed state:"
        grep "^${var}_" "$IIAB_STATE_FILE" | sed 's/^/  /'
    else
        echo "Installed state: not installed"
    fi
    if [ -f "$LOCAL_VARS_FILE" ] && grep -q "^${var}_install:" "$LOCAL_VARS_FILE"; then
        grep "^${var}_install:" "$LOCAL_VARS_FILE" | sed 's/^/  desired /'
    elif [ -f "$DEFAULT_VARS_FILE" ] && grep -q "^${var}_install:" "$DEFAULT_VARS_FILE"; then
        grep "^${var}_install:" "$DEFAULT_VARS_FILE" | sed 's/^/  default /'
    fi
    if [ -f "$LOCAL_VARS_FILE" ] && grep -q "^${var}_enabled:" "$LOCAL_VARS_FILE"; then
        grep "^${var}_enabled:" "$LOCAL_VARS_FILE" | sed 's/^/  desired /'
    elif [ -f "$DEFAULT_VARS_FILE" ] && grep -q "^${var}_enabled:" "$DEFAULT_VARS_FILE"; then
        grep "^${var}_enabled:" "$DEFAULT_VARS_FILE" | sed 's/^/  default /'
    fi
    if [ -f "$IIAB_INI_FILE" ] && grep -q "^${var}_version[[:space:]]*=" "$IIAB_INI_FILE"; then
        grep "^${var}_version[[:space:]]*=" "$IIAB_INI_FILE" | tail -1 | sed 's/^/  installed /'
    fi
    for feature in uninstall upgrade force_reinstall; do
        role_supports "$role" "$feature" && capabilities+=("$feature")
    done
    if [ ${#capabilities[@]} -eq 0 ]; then
        echo "Capabilities: none"
    else
        echo "Capabilities: ${capabilities[*]}"
    fi
    echo
}

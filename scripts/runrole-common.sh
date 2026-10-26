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

# A role opts into the destructive/extended operations by declaring them in
# roles/<role>/meta/runrole.yml, for example:
#
#     uninstall: true
#     upgrade: true
#     force_reinstall: true
role_manifest_file() {
    echo "roles/$1/meta/runrole.yml"
}

# True if <role> declares support for <feature> (uninstall, upgrade, or
# force_reinstall) in its manifest.
role_supports() {
    local manifest
    manifest=$(role_manifest_file "$1")
    [ -f "$manifest" ] || return 1
    grep -Eq "^${2}:[[:space:]]*([Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Oo][Nn])[[:space:]]*$" "$manifest"
}

# Print an error and return nonzero unless <role> declares <feature>.
role_require_capability() {
    local role=$1
    local flag=$2
    local feature=$3
    if role_supports "$role" "$feature"; then
        return 0
    fi
    echo -e "\e[1mERROR: role '$role' does not support $flag.\e[0m" >&2
    echo "Expected '$feature: true' in $(role_manifest_file "$role")." >&2
    return 1
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

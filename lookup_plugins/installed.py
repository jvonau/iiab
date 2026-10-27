"""Look up an optional IIAB state marker safely.

``*_installed`` (and similar) variables only exist after a role has written
them to /etc/iiab/iiab_state.yml.  Testing one directly raises when the role
was never installed.  Use this lookup for boolean checks:

    when: lookup('installed', 'mysql')

The lookup accepts either the role name or the full marker name, and returns
false when the marker is undefined.
"""

from __future__ import annotations

from ansible.module_utils.parsing.convert_bool import boolean
from ansible.plugins.lookup import LookupBase


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        variables = variables or {}
        results = []
        for term in terms:
            marker = str(term)
            if not marker.endswith(("_installed", "_downloaded")):
                marker += "_installed"
            value = variables.get(marker, False)
            try:
                results.append(boolean(value, strict=False))
            except (TypeError, ValueError):
                results.append(bool(value))
        return results

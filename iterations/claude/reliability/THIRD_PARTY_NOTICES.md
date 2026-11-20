# Third-party licenses and boundaries

These source archives do not bundle dependency libraries. Dependency
declarations are not a license grant over those dependencies. Preserve each
upstream license and notice when redistributing that dependency.

| Dependency inspected | Upstream license information |
| --- | --- |
| telnetlib3 4.0.3 | ISC; its upstream LICENSE.txt also retains BSD/NetBSD-derived SLC notices. Preserve the complete upstream license file. |
| mcp 2.3.0 | MIT; upstream copyright notice credits Anthropic, PBC. |
| cryptography 50.0.2 | Apache-2.0 OR BSD-3-Clause. Preserve upstream notices and applicable bundled-component terms. |
| jinxed 2.1.0 and ansicon 1.89.0 (Windows transitive dependencies) | MPL-2.0. jinxed also carries LICENSE.ncurses for derived terminfo data. |

The table records inspected direct pins and named Windows transitive packages,
not an exhaustive certification of every future resolved dependency or wheel.
Source ZIPs contain declarations/lock metadata, not these packages. A bundled
installer must inventory the actual dependencies and their notices separately.

Python, Tailscale and AI provider runtimes are external prerequisites with
their own terms. This project's Apache license does not relicense them.
No external MUD client fork or vendored library source was identified in the
120-file public baseline audited for the 0.2.1 notice release.

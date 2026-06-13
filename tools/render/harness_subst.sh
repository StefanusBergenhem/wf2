#!/usr/bin/env bash
#
# harness_subst.sh — the ONE place harness-specific naming lives.
#
# wf2 skill/agent prose is written once with neutral placeholders. At INSTALL
# time, install.sh sources this file and calls `wf_subst_file <target> <file>`
# to bake the concrete names for the chosen harness directly into the
# materialized skill/agent. The agent that later reads the installed file sees
# clean, single-target prose — no runtime branching, no adapter lookup.
#
# Two mechanisms, both resolved here:
#
#   1. Inline tokens   {{WF_*}}      → substituted with the per-target value below.
#   2. Conditional blocks            → only the matching branch is kept:
#        <!-- wf:if pi -->            (condition is a comma-separated target list)
#        …pi-only lines…
#        <!-- wf:else -->
#        …lines for the other targets…
#        <!-- wf:endif -->
#
# Add a token only when a skill/agent actually references it — keep this map as
# small as the prose demands (governor: no vocabulary nothing uses). To add one:
# define it for ALL three targets below, then add its sed arm in wf_subst_file.
# To support a new harness: add its case arm.
#
# Usage (sourced):
#   . harness_subst.sh
#   wf_subst_file claude path/to/SKILL.md      # edits the file in place

# Populate WF_* token values for a target into the caller's environment.
_wf_set_tokens() {
    local t="$1"
    WF_TARGET="$t"
    case "$t" in
        claude)   WF_SKILLS_DIR='.claude/skills' ;;
        pi)       WF_SKILLS_DIR='.pi/skills' ;;
        opencode) WF_SKILLS_DIR='.opencode/skills' ;;
        *) echo "harness_subst: unknown target '$t'" >&2; return 2 ;;
    esac
}

# wf_subst_file <target> <file> — strip non-matching conditional blocks, then
# substitute {{WF_*}} tokens, editing <file> in place.
wf_subst_file() {
    local t="$1" f="$2"
    _wf_set_tokens "$t" || return 2
    [ -f "$f" ] || { echo "wf_subst_file: no such file: $f" >&2; return 2; }

    awk -v T="$t" '
        function targmatch(list,   n,a,i){
            n=split(list,a,",");
            for(i=1;i<=n;i++){ gsub(/[[:space:]]/,"",a[i]); if(a[i]==T) return 1 }
            return 0
        }
        /<!--[[:space:]]*wf:if[[:space:]]/ {
            cond=$0; sub(/.*wf:if[[:space:]]*/,"",cond); sub(/[[:space:]]*-->.*/,"",cond);
            inblock=1; thisbranch=targmatch(cond); emit=thisbranch; next
        }
        /<!--[[:space:]]*wf:else[[:space:]]*-->/ { if(inblock){ emit=(thisbranch?0:1) } next }
        /<!--[[:space:]]*wf:endif[[:space:]]*-->/ { inblock=0; emit=1; next }
        { if(emit!=0) print }
        BEGIN{ emit=1 }
    ' "$f" \
    | sed -e "s|{{WF_SKILLS_DIR}}|${WF_SKILLS_DIR}|g" \
          -e "s|{{WF_TARGET}}|${WF_TARGET}|g" \
    > "$f.wftmp" && mv "$f.wftmp" "$f"
}

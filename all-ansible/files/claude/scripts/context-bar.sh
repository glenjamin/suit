#!/bin/bash

# Tests: python3 context_bar_test.py (alongside this script)

# Color theme: gray, orange, blue, teal, green, lavender, rose, gold, slate, cyan
# Preview colors with: bash scripts/color-preview.sh
COLOR="orange"

# Color codes
C_RESET='\033[0m'
C_GRAY='\033[38;5;245m'  # explicit gray for default text
C_BAR_EMPTY='\033[38;5;238m'
case "$COLOR" in
    orange)   C_ACCENT='\033[38;5;173m' ;;
    blue)     C_ACCENT='\033[38;5;74m' ;;
    teal)     C_ACCENT='\033[38;5;66m' ;;
    green)    C_ACCENT='\033[38;5;71m' ;;
    lavender) C_ACCENT='\033[38;5;139m' ;;
    rose)     C_ACCENT='\033[38;5;132m' ;;
    gold)     C_ACCENT='\033[38;5;136m' ;;
    slate)    C_ACCENT='\033[38;5;60m' ;;
    cyan)     C_ACCENT='\033[38;5;37m' ;;
    *)        C_ACCENT="$C_GRAY" ;;  # gray: all same color
esac

input=$(cat)

# Extract model, directory, and cwd
model=$(echo "$input" | jq -r '.model.display_name // .model.id // "?"')
cwd=$(echo "$input" | jq -r '.cwd // empty')
dir=$(basename "$cwd" 2>/dev/null || echo "?")

# Live effort level, tracking mid-session changes. Absent for models that
# don't take a reasoning effort, so the segment collapses to nothing there.
# Known levels abbreviate to one gray character butted against the model name,
# the gray doing the work a separator otherwise would.
effort=$(echo "$input" | jq -r '.effort.level // empty')
case "$effort" in
    low)    effort_char="l" ;;
    medium) effort_char="m" ;;
    high)   effort_char="h" ;;
    xhigh)  effort_char="x" ;;
    max)    effort_char="!" ;;
    *)      effort_char="$effort" ;;  # unrecognised level: pass through whole
esac
effort_segment=""
[[ -n "$effort_char" ]] && effort_segment="${C_GRAY}${effort_char}"

# Git branch and dirty status
git_segment=""
if [[ -n "$cwd" ]]; then
    branch=$(git -C "$cwd" symbolic-ref --short HEAD 2>/dev/null \
        || git -C "$cwd" rev-parse --short HEAD 2>/dev/null)
    if [[ -n "$branch" ]]; then
        dirty=""
        if [[ -n $(git -C "$cwd" status --porcelain 2>/dev/null) ]]; then
            dirty="*"
        fi

        # Real repo name: parent of the common git dir (shared by all worktrees)
        common_dir=$(git -C "$cwd" rev-parse --git-common-dir 2>/dev/null)
        [[ "$common_dir" != /* ]] && common_dir="$cwd/$common_dir"
        repo=$(basename "$(cd "$(dirname "$common_dir")" 2>/dev/null && pwd)")

        toplevel=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)
        git_dir=$(git -C "$cwd" rev-parse --git-dir 2>/dev/null)

        # (repo) prefixes the branch when we're not at a plain repo root.
        # Worktrees also get a ⧉ marker after the branch, plus the worktree
        # name when we're in a subdir (at its root the dir segment shows it).
        repo_prefix=""
        wt_suffix=""
        if [[ "$git_dir" == */worktrees/* ]]; then
            repo_prefix="(${repo}) "
            wt_suffix=" ⧉"
            [[ "$toplevel" != "$cwd" ]] && wt_suffix+=" $(basename "$toplevel")"
        elif [[ -n "$toplevel" && "$toplevel" != "$cwd" ]]; then
            repo_prefix="(${repo}) "
        fi
        git_segment=" ${C_GRAY}| ${C_ACCENT}${repo_prefix}${branch}${dirty}${wt_suffix}${C_RESET}"
    fi
fi

# Get used context percent from JSON
pct=$(echo "$input" | jq -r '.context_window.used_percentage // 0')

# Get context window size from JSON
max_context=$(echo "$input" | jq -r '.context_window.context_window_size // 200000')
if (( max_context >= 1000000 && max_context % 1000000 == 0 )); then
    max_label="$((max_context / 1000000))M"
else
    max_label="$((max_context / 1000))k"
fi

# Calculate context bar from transcript
bar_width=10

bar=""
for ((i=0; i<bar_width; i++)); do
    bar_start=$((i * 10))
    progress=$((pct - bar_start))
    if [[ $progress -ge 8 ]]; then
        bar+="${C_ACCENT}█${C_RESET}"
    elif [[ $progress -ge 3 ]]; then
        bar+="${C_ACCENT}▄${C_RESET}"
    else
        bar+="${C_BAR_EMPTY}░${C_RESET}"
    fi
done

ctx="${bar} ${C_GRAY}${pct_prefix}${pct}% of ${max_label}"

# Prompt cache expiry, estimated from the last API call in the transcript and
# the TTL it last wrote with. Only shown past 200k tokens of context, once
# expiry is close or past.
cache_segment=""
transcript=$(echo "$input" | jq -r '.transcript_path // empty')
used_tokens=$(echo "$input" | jq -r '.context_window.total_input_tokens // 0')
if [[ -f "$transcript" ]] && ((used_tokens > 200000)); then
    read -r ts ttl < <(tail -200 "$transcript" | jq -rs '
        [.[] | select(.type == "assistant" and .message.usage != null)
             | .message.usage as $u
             | select(($u.cache_read_input_tokens // 0) + ($u.cache_creation_input_tokens // 0) > 0)
             | {ts: .timestamp, h1: ($u.cache_creation.ephemeral_1h_input_tokens // 0),
                m5: ($u.cache_creation.ephemeral_5m_input_tokens // 0)}]
        | select(length > 0)
        | ([.[] | select(.h1 + .m5 > 0)] | last) as $w
        | [last.ts, (if ($w.h1 // 0) > 0 then 3600 else 300 end)] | @tsv' 2>/dev/null)
    if [[ -n "$ts" ]]; then
        last=$(date -j -u -f "%Y-%m-%dT%H:%M:%S" "${ts%.*}" +%s 2>/dev/null)
        if [[ -n "$last" ]]; then
            left=$((last + ttl - $(date +%s)))
            if ((left < 60)); then
                cache_segment=" ${C_GRAY}uncached"
            elif ((left < 900)); then
                cache_segment=" ${C_GRAY}exp $((left / 60))m"
            fi
        fi
    fi
fi

# Build output: Model+effort | Dir | Branch* | Context
output="${C_ACCENT}${model}${effort_segment}${C_GRAY} | ${dir}"
output+="${git_segment}"
output+="${C_GRAY} | ${ctx}${cache_segment}${C_RESET}"

printf '%b\n' "$output"

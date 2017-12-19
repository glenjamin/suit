# If not running interactively, don't do anything
[ -z "$PS1" ] && return

command_exists () {
    type "$1" &> /dev/null ;
}

# enable programmable completion features (you don't need to enable
# this, if it's already enabled in /etc/bash.bashrc and /etc/profile
# sources /etc/bash.bashrc).
if [ -f /etc/bash_completion ] && ! shopt -oq posix; then
    . /etc/bash_completion
fi

# don't put duplicate lines in the history. See bash(1) for more options
# ... or force ignoredups and ignorespace
HISTCONTROL=ignoredups:ignorespace

# append to the history file, don't overwrite it
shopt -s histappend

# for setting history length see HISTSIZE and HISTFILESIZE in bash(1)
HISTSIZE=100000
HISTFILESIZE=10000000

# Case insensitive tab completion
bind "set completion-ignore-case on"

# check the window size after each command and, if necessary,
# update the values of LINES and COLUMNS.
shopt -s checkwinsize

# make less more friendly for non-text input files, see lesspipe(1)
[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# set a fancy prompt (non-color, unless we know we "want" color)
case "$TERM" in
    xterm-color) color_prompt=yes;;
esac

if [ -x /usr/bin/tput ] && tput setaf 1 >&/dev/null; then
    # We have color support; assume it's compliant with Ecma-48
    # (ISO/IEC-6429). (Lack of such support is extremely rare, and such
    # a case would tend to support setf rather than setaf.)
    color_prompt=yes
else
    color_prompt=
fi

if [ -n "$CUSTOM_HOSTNAME" ]; then
    prompt_host="$CUSTOM_HOSTNAME"
else
    prompt_host="$(hostname -s)"
fi

if [ "$color_prompt" = yes ]; then
    if [ -n "$HOST_COLOUR" ]; then
	      c_host="\[\033$HOST_COLOUR\]"
    else
	      c_host="\[\033[01;36m\]"
    fi
    if [[ $EUID -ne 0 ]]; then
	      c_id="\[\033[01;32m\]"
    else
	      c_id="\[\033[01;31m\]"
    fi
    c_path="\[\033[01;34m\]"
    c_vcs="\[\033[01;33m\]"
    c_reset="\[\033[0m\]"
    git=""
    command_exists __git_ps1 && git="$c_vcs\$(__git_ps1)"
    PS_SPEC="$c_path\w$git $c_id\u$c_reset ○ $c_host$prompt_host$c_reset"
else
    PS_SPEC="\w\$(__git_ps1) \u ○ $prompt_host"
fi
unset color_prompt c_host c_id c_path c_vcs c_reset git

export PROMPT_COMMAND=__prompt_command

function __prompt_command() {
    local EXIT="$?"

    echo -ne "\033]0;$prompt_host: $(basename `pwd`)\007"

    local c_red="\[\033[01;31m\]"
    local c_gray="\[\033[01;30m\]"
    local c_reset="\[\033[0m\]"

    PS1="\n$c_gray$(date '+%a %-d %b %H:%M:%S')$c_reset"
    PS1+="\n$PS_SPEC"
    if [ $EXIT != 0 ]; then
        PS1+=" $c_red$EXIT$c_reset"
    fi
    PS1+="\n> "
}

# If this is an xterm set the title to user@host:dir
case "$TERM" in
xterm*|rxvt*)
    PS1="\[\e]0;\u@$prompt_host: \w\a\]$PS1"
    ;;
*)
    ;;
esac

# enable color support of ls and also add handy aliases
if [ -x /usr/bin/dircolors ]; then
    test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
    alias ls='ls --color=auto'
    #alias dir='dir --color=auto'
    #alias vdir='vdir --color=auto'

    alias grep='grep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias egrep='egrep --color=auto'
fi
if [ `uname` = "Darwin" ]; then
    alias ls='ls -G'

    alias grep='grep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias egrep='egrep --color=auto'
fi

# some more ls aliases
alias ll='ls -alF'
alias la='ls -A'
alias l='ls -CF'

# Add an "alert" alias for long running commands.  Use like so:
#   sleep 10; alert
alias alert='notify-send --urgency=low -i "$([ $? = 0 ] && echo terminal || echo error)" "$(history|tail -n1|sed -e '\''s/^\s*[0-9]\+\s*//;s/[;&|]\s*alert$//'\'')"'

# Muscle memory is a terrible thing
alias sub=atom

# List processes via pgrep, then prompt to pkill
pgk() {
    [ -z "$*" ] && echo 'Usage: pgk <pattern>' && return 1
    pgrep -fl $*
    [ "$?" == "1" ] && echo 'No processes match' && return 1
    echo 'Hit [Enter] to pkill, [Ctrl+C] to abort'
    read && pkill -f $*
}

# export everything from a .env file into current shell
dotenv() {
    local file="${1:-.env}"
    eval $(cat $file | egrep -v '^#|^\s*$' | sed 's/^/export /')
}

showcert() {
    echo "" | \
        openssl s_client -servername $1 -showcerts -connect $1:443 \
        | openssl x509 -inform pem -noout -text
}

# Per-host definitions
if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi

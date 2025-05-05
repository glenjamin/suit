function pgk --description 'List processes via pgrep, then prompt to pkill'

    [ -z "$argv" ] && echo 'Usage: pgk <pattern>' && return 1
    pgrep -fl $argv
    [ "$status" = "1" ] && echo 'No processes match' && return 1
    read --prompt-str 'Hit [Enter] to pkill, [Ctrl+C] to abort' \
      && pkill -f $argv
end

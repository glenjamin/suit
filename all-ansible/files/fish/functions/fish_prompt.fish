# TODO: starship.rs??

function fish_prompt --description 'Informative prompt'
	#Save the return status of the previous command
	set -l last_pipestatus $pipestatus
	set -lx __fish_last_status $status # Export for __fish_print_pipestatus.
    
    set -l user_color --bold green
    if functions -q fish_is_root_user; and fish_is_root_user
        set -l user_color --bold red
    end
    set -l host_color --bold cyan
    # TODO: custom colour for vagrant

    echo \n(set_color brblack)(date '+%a %-d %b %H:%M:%S')

    echo -n (set_color --bold blue)(prompt_pwd --full-length-dirs 5)
    echo -n (set_color --bold yellow)(fish_vcs_prompt)
    echo -n (set_color $user_color) $USER
    echo -n (set_color normal) ○
    echo -n (set_color $host_color) (prompt_hostname)
    echo '' (__fish_print_pipestatus '' '' ' | ' '' (set_color --bold red) $last_pipestatus)

    echo -n (set_color normal)'> ' 
end

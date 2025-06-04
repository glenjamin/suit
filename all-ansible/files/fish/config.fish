alias sub='code -g'

# Attempt to import any env vars exported from profile.d
for file in /etc/profile.d/*.sh
  grep 'export' $file | sed -e 's/export/set -x/' -e 's/=/ /' | source
end

# Like conf.d, but these are in git
for file in ~/.config/fish/shared.conf.d/*.fish
  source $file
end

if status is-interactive

end

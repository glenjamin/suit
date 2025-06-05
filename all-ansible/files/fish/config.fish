alias sub='code -g'

# Like conf.d, but these are in git
for file in ~/.config/fish/shared.conf.d/*.fish
  source $file
end

if status is-interactive

end

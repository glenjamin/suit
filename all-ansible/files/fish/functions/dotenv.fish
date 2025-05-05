function dotenv --description 'export everything from a .env file into current shell'
    if set -q $argv[1]
      set file $argv[1]
    else
      set file .env
    end

    if ! test -e $file
      echo "$file not found"
      return 1
    end

    egrep -v '^#|^\s*$' $file | sed -e 's/=/ /' -e 's/^/set -gx /' | source
end

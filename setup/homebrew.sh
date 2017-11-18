# Use the install method from the homebrew website
/usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"

export PATH=/usr/local/bin:$PATH

# install homebrew bundle, cask, services and versions
brew bundle --file=- <<EOF
    tap 'caskroom/cask'
    tap 'homebrew/core'
    tap 'homebrew/services'
    tap 'homebrew/versions'
EOF

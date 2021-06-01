# Usage

```
ansible-galaxy install -r requirements.yml
```

```
 ansible-playbook -v foo.yml
```

# Bootstrap

To set up on a fresh Mac:

Generate some SSH Keys and then add them to the keychain ssh agent
```
ssh-keygen -t ed25519 -a 100 -C `hostname`
ssh-add -K
```

Sign into the Mac App Store

Clone the repo (accept the prompt to install dev tools)
```sh
git clone git@github.com:glenjamin/suit.git
```

Install ansible
```sh
sudo pip3 install --upgrade pip
sudo pip3 install ansible
``` 

Install homebrew
```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

# TODO

Playbooks: split into focus areas
- base
- dev
- apps
- python3
- clojure
- golang
- nodejs

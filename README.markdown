Setting up a new macOS computer
===============================

The initial software and code setup for my personal computers,
for use with [suited](https://github.com/norm/suited).

## macOS account setup

Create the usual `glen` account and sign in to iCloud

## Software installation and environment customisation

Generate some SSH Keys and then add them to the keychain ssh agent
```
ssh-keygen -t ed25519 -a 100 -C `hostname`
ssh-add -K
```

Explicitly sign into the Mac App Store application: Store → Sign In…

Run suited:
```
curl -O https://raw.githubusercontent.com/norm/suited/master/suited.sh
bash suited.sh github:glenjamin/suit:initial_setup.conf
```

Reboot once suited has run successfully all the way through.


Run suited again, now installed to `/usr/local/bin`:

```
# set up the mac with my stuff
suited github:glenjamin/suit:main.conf
```


## Troubleshooting

Using the Node.js binary with `n` has code signing issues.

See https://github.com/tj/n/issues/394#issuecomment-386807868 for the workaround

```
sudo codesign --force --sign - /usr/local/bin/node
```

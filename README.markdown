Setting up a new macOS computer
===============================

The initial software and code setup for my personal computers,
for use with [suited](https://github.com/norm/suited).

## macOS account setup

???

## Software installation and environment customisation

Generate

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
